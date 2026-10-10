"""Anthropic Messages 传输适配器（E3）—— GLM Coding Plan ``/api/anthropic`` 先行。

与 OpenAI-compatible 的 wire 差异全部在本 adapter 内映射到现有
:class:`ChatRequest` / :class:`ChatResponse`（bridge 协议、prompt
SECTION_ORDER、工具层零改动）：

* system 走顶层 ``system`` 参数；连续 ``role=tool`` 结果合并进同一条 user
  消息的 ``tool_result`` blocks（Anthropic 要求 tool_result 紧跟 assistant
  的 tool_use）；assistant ``tool_calls`` → ``tool_use`` blocks；
* **thinking 回放清理**：``reasoning_content`` 一律不跨传输回放 —— Anthropic
  要求 thinking 块为原生完整块，外来的思考轨迹只能丢弃（档位事实见
  :mod:`modelbridge.model_catalog`）；请求级 ``thinking`` 开关映射为
  ``thinking: {"type": "enabled", "budget_tokens": N}``；
* tools: OpenAI function schema → ``input_schema``；``tool_choice: "auto"``
  → ``{"type": "auto"}``；``max_tokens`` Anthropic 必填，缺省 4096；
* SSE: ``message_start`` / ``content_block_start`` / ``content_block_delta``
  (text_delta · input_json_delta · thinking_delta) / ``message_delta``
  (stop_reason + output usage) / ``message_stop`` / ``error``；
* usage: ``input_tokens``/``output_tokens`` → ``prompt_tokens``/
  ``completion_tokens``，``cache_read_input_tokens``/
  ``cache_creation_input_tokens`` 原样透传。

重试复用 U5/E1 骨架（:meth:`BaseProvider._retry_delays` +
:meth:`BaseProvider._sleep_before_retry`，Retry-After 优先）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from ..error_hints import is_retryable_error
from ..schemas import (
    ChatRequest,
    ChatResponse,
    ProviderError,
    text_of,
)
from . import base as _base
from .base import (
    BaseProvider,
    _parse_retry_after,
    StreamEvent,
)

#: Anthropic 必填的 max_tokens 兜底（请求未指定时）。
_DEFAULT_MAX_TOKENS = 4096
#: thinking.budget_tokens 的下限（Anthropic 文档约束 ≥1024）。
_MIN_THINKING_BUDGET = 1024

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "stop",
}


class AnthropicMessagesProvider(BaseProvider):
    """``type: anthropic_messages`` 模型的 wire adapter。"""

    name = "anthropic-messages"

    def chat_endpoint(self) -> str:
        base = self.entry.base_url.rstrip("/")
        if base.endswith("/messages"):
            return base
        return f"{base}/v1/messages"

    def build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "User-Agent": "ModelBridge (mbridge)",
        }
        if self.api_key and self.api_key != "EMPTY":
            headers["x-api-key"] = self.api_key
        return headers

    # ------------------------------------------------------------------
    # 请求映射
    # ------------------------------------------------------------------

    def build_chat_payload(self, request: ChatRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                messages.append(
                    {"role": "user", "content": list(pending_tool_results)}
                )
                pending_tool_results.clear()

        for m in request.messages:
            if m.role == "system":
                text = text_of(m.content)
                if text:
                    system_parts.append(text)
                continue
            if m.role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    "content": text_of(m.content),
                })
                continue
            flush_tool_results()
            if m.role == "assistant":
                blocks: list[dict[str, Any]] = []
                text = text_of(m.content)
                if text:
                    blocks.append({"type": "text", "text": text})
                for tc in m.tool_calls or []:
                    fn = tc.get("function") or {}
                    try:
                        input_obj = json.loads(fn.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        input_obj = {}
                    if not isinstance(input_obj, dict):
                        input_obj = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "input": input_obj,
                    })
                # thinking 回放清理：reasoning_content 刻意丢弃（见模块 docstring）。
                if blocks:
                    messages.append({"role": "assistant", "content": blocks})
                continue
            # user / 多模态（本传输只取 text 块；image 走 OpenAI-compatible 传输）
            text = text_of(m.content)
            if text:
                messages.append({
                    "role": "user", "content": [{"type": "text", "text": text}],
                })
        flush_tool_results()

        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or _DEFAULT_MAX_TOKENS,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if messages:
            payload["messages"] = messages
        if request.tools:
            tools: list[dict[str, Any]] = []
            for t in request.tools:
                fn = (t.get("function") or {}) if isinstance(t, dict) else {}
                if not fn.get("name"):
                    continue
                tools.append({
                    "name": fn["name"],
                    "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters") or {
                        "type": "object", "properties": {},
                    },
                })
            if tools:
                payload["tools"] = tools
                if request.tool_choice == "auto":
                    payload["tool_choice"] = {"type": "auto"}
        if request.thinking:
            # Anthropic thinking 的两条硬约束（评审修复）：
            # ① temperature 只能省略或为 1 —— thinking 启用时丢弃用户配置
            #    的 temperature（否则特定组合必然 400）；
            # ② max_tokens 必须严格大于 budget_tokens —— 先钳 budget 到
            #    [1024, max_tokens-1]，仍不足时抬高 max_tokens = budget+1。
            budget = request.thinking_budget or 8192
            budget = max(_MIN_THINKING_BUDGET, min(int(budget), payload["max_tokens"] - 1))
            if budget >= payload["max_tokens"]:
                payload["max_tokens"] = budget + 1
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif request.temperature is not None:
            payload["temperature"] = request.temperature
        for k, v in (request.extra_body or {}).items():
            payload[k] = v
        return payload

    # ------------------------------------------------------------------
    # 响应映射
    # ------------------------------------------------------------------

    def parse_chat_response(self, data: dict[str, Any]) -> ChatResponse:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for b in data.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                text_parts.append(b.get("text") or "")
            elif b.get("type") == "tool_use":
                try:
                    arg_str = json.dumps(b.get("input") or {}, ensure_ascii=False)
                except (TypeError, ValueError):
                    arg_str = "{}"
                tool_calls.append({
                    "id": b.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": b.get("name") or "",
                        "arguments": arg_str,
                    },
                })
        raw_usage = data.get("usage") or {}
        usage: dict[str, Any] = {}
        if raw_usage.get("input_tokens") is not None:
            usage["prompt_tokens"] = raw_usage["input_tokens"]
        if raw_usage.get("output_tokens") is not None:
            usage["completion_tokens"] = raw_usage["output_tokens"]
        for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if raw_usage.get(k) is not None:
                usage[k] = raw_usage[k]
        return ChatResponse(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            usage=usage or None,
            model=data.get("model"),
            provider=self.name,
            finish_reason=_STOP_REASON_MAP.get(
                data.get("stop_reason") or "", "stop"
            ),
            raw=data,
        )

    # ------------------------------------------------------------------
    # 传输（chat 重试骨架与 HTTPProvider 一致）
    # ------------------------------------------------------------------

    def chat(
        self,
        request: ChatRequest,
        *,
        timeout: float = 60.0,
        save_raw: bool = False,
        verbose_label: str = "chat",
    ) -> ChatResponse:
        delays = self._retry_delays()
        attempt = 0
        while True:
            try:
                return self._chat_once(
                    request, timeout=timeout, save_raw=save_raw,
                    verbose_label=verbose_label,
                )
            except ProviderError as e:
                if attempt >= len(delays) or not is_retryable_error(e):
                    raise
                attempt += 1
                self._sleep_before_retry(e, delays, attempt)

    def _chat_once(
        self,
        request: ChatRequest,
        *,
        timeout: float = 60.0,
        save_raw: bool = False,
        verbose_label: str = "chat",
    ) -> ChatResponse:
        endpoint = self.chat_endpoint()
        headers = self.build_headers()
        body = self.build_chat_payload(request)

        start = time.perf_counter()
        try:
            client = _base._shared_http_client(endpoint)
            resp = client.post(endpoint, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise self.normalize_error(exc=exc) from exc

        if resp.status_code >= 400:
            err = self.normalize_error(status_code=resp.status_code, body=resp.text)
            err.retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            raise err
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "响应不是有效的 JSON",
                provider=self.name,
                status_code=resp.status_code,
                error_type="decode",
                hint="Anthropic-compatible 端点返回了非 JSON 响应。",
                raw=resp.text[:500],
            ) from exc
        chat_resp = self.parse_chat_response(data)
        chat_resp.elapsed_ms = int((time.perf_counter() - start) * 1000)
        return chat_resp

    # ------------------------------------------------------------------
    # SSE
    # ------------------------------------------------------------------

    def stream_chat(
        self, request: ChatRequest, *, timeout: float = 120.0,
    ) -> Iterator[StreamEvent]:
        endpoint = self.chat_endpoint()
        headers = self.build_headers()
        body = self.build_chat_payload(request)
        body["stream"] = True

        client = _base._shared_http_client(endpoint)
        # E1①: 流空闲看门狗（与 OpenAI-compatible 流路径同一语义）。
        stream_timeout = httpx.Timeout(
            connect=10.0, read=max(30.0, float(timeout)), write=30.0, pool=10.0,
        )
        try:
            stream_cm = client.stream(
                "POST", endpoint, headers=headers, json=body, timeout=stream_timeout,
            )
        except httpx.HTTPError as exc:
            raise self.normalize_error(exc=exc) from exc

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tools: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        current_tool_index: int | None = None
        start = time.perf_counter()

        with stream_cm as resp:
            if resp.status_code >= 400:
                body_text = resp.read().decode("utf-8", errors="replace")
                err = self.normalize_error(status_code=resp.status_code, body=body_text)
                err.retry_after = _parse_retry_after(
                    resp.headers.get("Retry-After")
                )
                raise err
            for raw_line in resp.iter_lines():
                line = (
                    raw_line if isinstance(raw_line, str)
                    else raw_line.decode("utf-8", errors="replace")
                )
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].lstrip()
                if not line or line == "[DONE]":
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")

                if etype == "error":
                    sub = ev.get("error") or {}
                    raise ProviderError(
                        str(sub.get("message") or etype),
                        provider=self.name,
                        error_type="business_error",
                        hint="Anthropic 流内错误帧（HTTP 200）。"
                             "overloaded/rate_limit 类稍后重试通常可恢复。",
                    )

                if etype == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    if u.get("input_tokens") is not None:
                        usage["prompt_tokens"] = u["input_tokens"]
                    continue

                if etype == "content_block_start":
                    block = ev.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        current_tool_index = int(ev.get("index", 0))
                        tools[current_tool_index] = {
                            "id": block.get("id") or "",
                            "name": block.get("name") or "",
                            "raw_json": "",
                        }
                    continue

                if etype == "content_block_delta":
                    delta = ev.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta" and delta.get("text"):
                        content_parts.append(delta["text"])
                        yield StreamEvent(kind="content", text=delta["text"])
                    elif dtype == "thinking_delta" and delta.get("thinking"):
                        reasoning_parts.append(delta["thinking"])
                        yield StreamEvent(kind="reasoning", text=delta["thinking"])
                    elif dtype == "input_json_delta" and current_tool_index is not None:
                        slot = tools[current_tool_index]
                        slot["raw_json"] += delta.get("partial_json") or ""
                    continue

                if etype == "content_block_stop":
                    current_tool_index = None
                    continue

                if etype == "message_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("stop_reason"):
                        finish_reason = _STOP_REASON_MAP.get(
                            delta["stop_reason"], "stop"
                        )
                    u = ev.get("usage") or {}
                    if u.get("output_tokens") is not None:
                        usage["completion_tokens"] = u["output_tokens"]
                    continue

                if etype == "message_stop":
                    break

        elapsed = int((time.perf_counter() - start) * 1000)
        tool_calls: list[dict[str, Any]] = []
        for _, slot in sorted(tools.items()):
            try:
                input_obj = json.loads(slot["raw_json"] or "{}")
            except (TypeError, ValueError):
                input_obj = {}
            if not isinstance(input_obj, dict):
                input_obj = {}
            tool_calls.append({
                "id": slot["id"], "type": "function",
                "function": {"name": slot["name"],
                             "arguments": json.dumps(input_obj, ensure_ascii=False)},
            })
        raw_message: dict[str, Any] = {"role": "assistant",
                                       "content": "".join(content_parts)}
        resp_obj = ChatResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
            raw={"format": "anthropic_messages"},
            raw_message=raw_message,
            usage=usage or None,
            model=self.entry.model,
            provider=self.name,
            finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
            elapsed_ms=elapsed,
        )
        yield StreamEvent(kind="done", response=resp_obj)


__all__ = ["AnthropicMessagesProvider"]
