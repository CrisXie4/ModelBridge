"""Abstract base for all provider adapters.

A provider adapter:

* Knows how to translate a :class:`ChatRequest` into the wire payload
  expected by its provider, and back into a :class:`ChatResponse`.
* Owns the HTTP transport (currently httpx) so the CLI never touches
  ``httpx`` directly.
* Reports provider-flavoured error hints via :meth:`normalize_error`.
* Exposes a cheap :meth:`health_check` for the doctor.

Adapters should subclass :class:`BaseProvider` (or :class:`HTTPProvider` for
HTTP transports) and override the points where their provider differs from
the OpenAI spec.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from ..cache.affinity import sanitize_cache_key
from ..error_hints import (
    classify_business_code,
    classify_error_type,
    hint_for_exception,
    hint_for_http_error,
    is_retryable_error,
)
from ..models import ModelEntry, ProviderType
from ..raw_logger import save_raw_exchange
from ..schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderError,
)
from ..secrets import reveal
from ..utils import resolve_api_key


# ---------------------------------------------------------------------------
# 业务错误体识别（U10）
#
# 部分 OpenAI-compatible 厂商（智谱系）只在 HTTP 200 的 JSON/SSE error frame
# 里报业务错误，且字段名不统一（msg/error_code/code、request_id/requestId/id）。
# 判据对齐 ZCode readProviderBusinessFailureFromBody + detectProviderBusinessSse
# FrameFailure：success:false / 非零 code·error_code / event:error / [NNNN]
# 方括号文案。正常数据帧与 usage-only 帧永远不受影响（纯加法）。
# ---------------------------------------------------------------------------

_BRACKETED_CODE = re.compile(r"^\[(\d{4})\]")

#: Retry-After 视为"合理"的上限（秒）——超过 5min 的等待对交互式 REPL 没有
#: 意义，回落到本地指数退避（E1③）。
_RETRY_AFTER_MAX_S = 300.0


def _parse_retry_after(value: Any) -> float | None:
    """解析 ``Retry-After`` 响应头（仅秒数形式；HTTP-date 返回 None）。

    非法/非正数一律 None —— 宁可走本地退避，不要 sleep 出负数或天文数字。
    """
    if value is None:
        return None
    try:
        secs = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if secs <= 0 or secs != secs:  # NaN 防御
        return None
    return secs


def _first_str(*values: Any) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _first_code(*values: Any) -> str:
    """取第一个非零/非空的 code（int 或非 "0" 数字串）。"""
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, int) and v != 0:
            return str(v)
        if isinstance(v, str) and v.strip() and v.strip() != "0":
            return v.strip()
    return ""


def detect_business_error(data: Any) -> dict[str, str] | None:
    """识别业务错误体；正常帧 / usage 帧 / 非 dict 返回 ``None``。"""
    if not isinstance(data, dict):
        return None
    raw_err = data.get("error")
    err: dict = raw_err if isinstance(raw_err, dict) else {}
    code = _first_code(
        data.get("error_code"), err.get("error_code"),
        data.get("code"), err.get("code"),
    )
    message = _first_str(
        data.get("msg"), data.get("message"),
        err.get("msg"), err.get("message"),
    )
    has_choices = isinstance(data.get("choices"), list) and len(data["choices"]) > 0
    # 有非空 choices 且 success 非 false 的帧一律按正常数据处理 —— 部分
    # OpenAI-compatible 网关在成功响应体里也放 HTTP 风格 code:200 或非零
    # 业务码，不能因为结构化 code 就把正常帧拒掉（与方括号分支的豁免一致）。
    if has_choices and data.get("success") is not False:
        return None
    bracketed = _BRACKETED_CODE.match(message)
    if not code and bracketed and not has_choices:
        # 智谱系 SSE error chunk 有时只有 "[1302][...][request_id]" 文案；
        # 只认这个强格式前缀，避免把普通限流文案误判成业务码。
        code = bracketed.group(1)
    failed = data.get("success") is False or bool(code)
    if not failed:
        return None
    request_id = _first_str(
        data.get("request_id"), data.get("requestId"), data.get("id"),
        err.get("request_id"), err.get("requestId"), err.get("id"),
    )
    return {"code": code, "message": message, "request_id": request_id}


def provider_error_from_business(
    biz: dict[str, str], *, provider: str, status_code: int | None = 200,
) -> ProviderError:
    """把 :func:`detect_business_error` 的结果转成带分类 hint 的 ProviderError。"""
    code = biz.get("code") or ""
    message = biz.get("message") or ""
    error_type, hint, _retryable = classify_business_code(code, message)
    rid = biz.get("request_id") or None
    if rid:
        hint = f"{hint} [request_id={rid} — 报障必填]"
    shown = f"{code} {message}".strip() or "厂商返回业务错误"
    return ProviderError(
        shown,
        provider=provider,
        status_code=status_code,
        error_type=error_type,
        hint=hint,
        raw=biz,
        request_id=rid,
    )


class BaseProvider(ABC):
    """Base class for provider adapters."""

    #: Human-readable provider name (override in subclasses).
    name: str = "openai-compatible"

    #: Logical :class:`ProviderType` this adapter handles.
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE

    def __init__(self, entry: ModelEntry) -> None:
        self.entry = entry
        # ``entry.api_key`` may be a ``keyring:`` / ``enc:`` token — reveal it
        # to the real secret before the env-var fallback resolution.
        resolved = resolve_api_key(reveal(entry.name, entry.api_key), entry.api_key_env)
        if not resolved and entry.capabilities.local:
            resolved = "EMPTY"
        self.api_key = resolved

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def health_check(self, *, timeout: float = 5.0) -> tuple[bool, str]:
        """Cheap reachability probe via ``GET {base_url}/models``.

        Subclasses may override for provider-specific health endpoints.
        Returns ``(ok, detail)``.
        """
        url = self.entry.base_url.rstrip("/")
        try:
            client = _shared_http_client(url)
            resp = client.get(f"{url}/models", headers=self.build_headers(), timeout=timeout)
            if resp.status_code < 500:
                return True, f"GET /models → {resp.status_code}"
            return False, f"GET /models → {resp.status_code}"
        except httpx.HTTPError as e:
            return False, hint_for_exception(e, provider=self.name)

    # ------------------------------------------------------------------
    # Default OpenAI-style request building
    # ------------------------------------------------------------------

    def build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ModelBridge/0.2 (mbridge)",
        }
        if self.api_key and self.api_key != "EMPTY":
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat_endpoint(self) -> str:
        base = self.entry.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def build_chat_payload(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [self._serialize_message(m) for m in request.messages],
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.tools:
            body["tools"] = request.tools
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice
        if request.response_format is not None:
            body["response_format"] = request.response_format
        # model-level defaults from models.yaml (don't overwrite explicit fields)
        for k, v in (self.entry.extra or {}).items():
            if k == "extra_body" and isinstance(v, dict):
                for ek, ev in v.items():
                    body.setdefault(ek, ev)
            else:
                body.setdefault(k, v)
        # per-request extras override
        for k, v in (request.extra_body or {}).items():
            body[k] = v
        # Cache-affinity key: injected only on models that name a body field
        # via ``extra.cache_key_field`` (e.g. OpenAI-style ``prompt_cache_key``
        # custom endpoints). Explicit values merged above always win.
        # DeepSeek deliberately declares none — its wire format has no body
        # cache key and every provider+model pair is its own cache domain.
        cache_field = (self.entry.extra or {}).get("cache_key_field")
        if (
            cache_field
            and isinstance(cache_field, str)
            and request.cache_key
            and cache_field not in body
        ):
            body[cache_field] = sanitize_cache_key(request.cache_key)
        return body

    def _serialize_message(self, m: ChatMessage) -> dict[str, Any]:
        # MiMo / DeepSeek-reasoner / Kimi-thinking REQUIRE reasoning_content
        # to survive on assistant turns that contain tool_calls. Default
        # keeps it — adapters with stricter servers can override.
        return m.to_wire()

    def parse_chat_response(self, data: dict[str, Any]) -> ChatResponse:
        # U10: 200 也可能带业务错误体（success:false / 非零 code·error_code）
        # —— 先识别并转成带分类 hint 的 ProviderError，再走正常 choices 解析。
        biz = detect_business_error(data)
        if biz is not None:
            raise provider_error_from_business(
                biz, provider=self.name, status_code=200
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                "响应中没有 choices",
                provider=self.name,
                error_type="decode",
                hint="endpoint 可能不是 OpenAI-compatible，或上游返回了空响应。",
                raw=data,
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            # A 2xx body with valid JSON but a non-OpenAI shape (e.g.
            # ``{"choices": ["err"]}`` or ``[null]``) must surface as an
            # actionable decode error, not a raw KeyError/AttributeError
            # crash that escapes the provider layer.
            raise ProviderError(
                "响应 choices[0] 不是对象",
                provider=self.name,
                error_type="decode",
                hint="endpoint 可能不是 OpenAI-compatible。",
                raw=data,
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        return ChatResponse(
            content=content,
            reasoning_content=message.get("reasoning_content"),
            tool_calls=message.get("tool_calls"),
            raw=data,
            raw_message=message,
            usage=data.get("usage"),
            model=data.get("model"),
            provider=self.name,
            finish_reason=choice.get("finish_reason"),
        )

    # ------------------------------------------------------------------
    # Error normalisation
    # ------------------------------------------------------------------

    def normalize_error(
        self,
        *,
        status_code: int | None = None,
        body: str | None = None,
        exc: BaseException | None = None,
    ) -> ProviderError:
        # 注意：多个厂商子类覆写本方法（前置自家提示），签名保持三参不变；
        # Retry-After 由调用方在返回的 ProviderError 上直接挂
        # ``retry_after`` 属性（E1③），不走参数。
        if exc is not None:
            return ProviderError(
                str(exc) or type(exc).__name__,
                provider=self.name,
                error_type=classify_error_type(exc),
                hint=hint_for_exception(exc, provider=self.name),
                raw=body,
            )
        sc = status_code or 0
        msg = self._extract_error_message(body) or f"HTTP {sc}"
        err = ProviderError(
            f"{sc} {msg}",
            provider=self.name,
            status_code=sc,
            error_type=classify_error_type(sc),
            hint=hint_for_http_error(
                sc, provider=self.name, body=body, model=self.entry.name
            ),
            raw=body,
        )
        # U10 兜底链：错误体里如有业务码 → 换用分类 hint（入表的业务码给出
        # 限流可重试/额度终止/上下文超限等明确指引）；request_id 提出来进
        # ProviderError 与 hint（报障必填）。
        try:
            data = json.loads(body) if body else None
        except (ValueError, TypeError):
            data = None
        biz = detect_business_error(data) if isinstance(data, dict) else None
        if biz is not None:
            if biz.get("code"):
                biz_err = provider_error_from_business(
                    biz, provider=self.name, status_code=sc
                )
                err.error_type = biz_err.error_type
                err.hint = biz_err.hint
            if biz.get("request_id"):
                err.request_id = biz["request_id"]
                if not biz.get("code"):  # hint 已由 provider_error_from_business 带上
                    err.hint = (
                        f"{err.hint or ''} "
                        f"[request_id={biz['request_id']} — 报障必填]"
                    ).strip()
        return err

    @staticmethod
    def _extract_error_message(body: str | None) -> str | None:
        if not body:
            return None
        try:
            j = json.loads(body)
        except (ValueError, TypeError):
            return body[:200] if isinstance(body, str) else None
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict):
                for key in ("message", "msg"):
                    m = err.get(key)
                    if isinstance(m, str) and m:
                        return m
            for key in ("message", "msg"):
                m = j.get(key)
                if isinstance(m, str) and m:
                    return m
        return None

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 有限重试骨架（E1/U5）—— HTTPProvider 与 Anthropic Messages adapter 共用
    # ------------------------------------------------------------------

    def _retry_delays(self) -> list[float]:
        """预计算每次重试的退避秒数（指数 + jitter，封顶）。配置不可用视为关闭。"""
        try:
            from ..config import load_app_config

            cfg = load_app_config().prompt
            retries = max(0, int(cfg.model_retry_max_retries))
            base_ms = max(0, int(cfg.model_retry_base_delay_ms))
            max_ms = max(base_ms, int(cfg.model_retry_max_delay_ms))
        except Exception:  # noqa: BLE001 — 配置读不出时保持旧行为（不重试）
            return []
        delays: list[float] = []
        for i in range(retries):
            backoff = (base_ms / 1000.0) * (2 ** i)
            jittered = backoff * random.uniform(0.75, 1.25)
            delays.append(min(jittered, max_ms / 1000.0))
        return delays

    @staticmethod
    def _sleep_before_retry(err: ProviderError, delays: list[float], attempt: int) -> float:
        """第 ``attempt``（1-based）次重试前的等待：Retry-After 优先（≤5min），
        本地指数退避兜底。返回实际等待秒数。"""
        delay = delays[attempt - 1] if 0 < attempt <= len(delays) else 0.0
        # E1③: Retry-After 优先 —— 厂商标注了合理（≤5min）的等待时长时以它
        # 为准，避免与厂商节奏打架。
        ra = getattr(err, "retry_after", None)
        if isinstance(ra, (int, float)) and 0 < float(ra) <= _RETRY_AFTER_MAX_S:
            delay = float(ra)
        time.sleep(delay)
        return delay

    @abstractmethod
    def chat(
        self,
        request: ChatRequest,
        *,
        timeout: float = 60.0,
        save_raw: bool = False,
        verbose_label: str = "chat",
    ) -> ChatResponse:
        """Execute a chat completion."""


class HTTPProvider(BaseProvider):
    """httpx-based concrete base used by every OpenAI-compatible adapter."""

    def chat(
        self,
        request: ChatRequest,
        *,
        timeout: float = 60.0,
        save_raw: bool = False,
        verbose_label: str = "chat",
    ) -> ChatResponse:
        """Execute a chat completion with bounded transient-error retries.

        只重试"等一下重试有意义"的失败（429 / 5xx / 超时 / 连接与传输类，
        由 :func:`error_hints.is_retryable_error` 判定）；其余 4xx 语义错误
        与 decode 错误原样抛出。指数退避（base * 2^n，±25% jitter，单次封顶
        max_delay_ms），次数/间隔来自 ``prompt.model_retry_*`` 配置——默认
        保守的重试 2 次，``model_retry_max_retries: 0`` 关闭。stream_chat
        不在此包装（SSE 中途恢复是另一套机制）。
        """
        delays = self._retry_delays()
        attempt = 0
        while True:
            try:
                return self._chat_once(
                    request,
                    timeout=timeout,
                    save_raw=save_raw,
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
        response_status: int | None = None
        response_raw: Any = None
        error_dict: dict[str, Any] | None = None
        try:
            try:
                # Pooled client: reuses the TCP+TLS connection across turns
                # (a per-call Client paid a full handshake every request —
                # 100-300ms of pure latency on cn endpoints).
                client = _shared_http_client(endpoint)
                resp = client.post(endpoint, headers=headers, json=body, timeout=timeout)
            except httpx.HTTPError as exc:
                err = self.normalize_error(exc=exc)
                error_dict = err.to_dict()
                raise err from exc

            response_status = resp.status_code
            text = resp.text

            if resp.status_code >= 400:
                err = self.normalize_error(status_code=resp.status_code, body=text)
                # E1③: Retry-After 挂到错误对象（重试包装优先于本地退避使用）。
                err.retry_after = _parse_retry_after(
                    resp.headers.get("Retry-After")
                )
                error_dict = err.to_dict()
                raise err

            try:
                response_raw = resp.json()
            except ValueError as exc:
                err = ProviderError(
                    "响应不是有效的 JSON",
                    provider=self.name,
                    status_code=resp.status_code,
                    error_type="decode",
                    hint="endpoint 可能不是 OpenAI-compatible，或返回了 HTML 错误页。",
                    raw=text[:500],
                )
                error_dict = err.to_dict()
                raise err from exc

            chat_resp = self.parse_chat_response(response_raw)
            chat_resp.elapsed_ms = int((time.perf_counter() - start) * 1000)
            chat_resp.provider = self.name
            return chat_resp
        finally:
            if save_raw:
                save_raw_exchange(
                    model_name=self.entry.name,
                    provider=self.name,
                    base_url=self.entry.base_url,
                    endpoint=endpoint,
                    request_headers=headers,
                    request_body=body,
                    response_status=response_status,
                    response_raw=response_raw,
                    error=error_dict,
                    label=verbose_label,
                )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def stream_chat(
        self,
        request: ChatRequest,
        *,
        timeout: float = 120.0,
    ) -> "Iterator[StreamEvent]":
        """Yield :class:`StreamEvent` deltas as the model produces them.

        Returns one final ``StreamEvent(kind="done")`` that carries a
        ready-to-store :class:`ChatResponse` reconstructed from the
        chunks (so callers don't need to keep their own accumulator).
        """
        endpoint = self.chat_endpoint()
        headers = self.build_headers()
        body = self.build_chat_payload(request)
        body["stream"] = True
        # Ask OpenAI-compatible servers to include usage with the final chunk
        # (DeepSeek / Kimi honour this; local stacks may ignore it harmlessly).
        body.setdefault("stream_options", {"include_usage": True})

        acc = _StreamAccumulator(model_default=self.entry.model, provider=self.name)
        start = time.perf_counter()

        client = _shared_http_client(endpoint)
        # E1①: 流空闲看门狗 —— 显式 per-request Timeout。httpx 的 read 超时
        # 语义就是"两次收到数据之间的最大间隔"，即逐块看门狗：厂商流挂起时
        # 最多 idle 秒抛 ReadTimeout（timeout 类，可重试），REPL 不再永久
        # 阻塞。idle 取请求超时与 30s 的较大者（≤1s 的极小请求也至少给流
        # 30s 的块间隔）。
        stream_timeout = httpx.Timeout(
            connect=10.0, read=max(30.0, float(timeout)), write=30.0, pool=10.0,
        )
        try:
            stream_cm = client.stream(
                "POST", endpoint, headers=headers, json=body, timeout=stream_timeout
            )
        except httpx.HTTPError as exc:
            raise self.normalize_error(exc=exc) from exc

        with stream_cm as resp:
            if resp.status_code >= 400:
                body_text = resp.read().decode("utf-8", errors="replace")
                err = self.normalize_error(status_code=resp.status_code, body=body_text)
                err.retry_after = _parse_retry_after(
                    resp.headers.get("Retry-After")
                )
                raise err

            try:
                for raw_line in resp.iter_lines():
                    line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith(":"):  # SSE keep-alive comment
                        continue
                    if line.startswith("data:"):
                        line = line[5:].lstrip()
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        # Some servers send mid-stream warnings on
                        # their own lines; tolerate and move on.
                        continue
                    for ev in acc.consume(chunk):
                        if ev.kind == "error" and ev.error is not None:
                            # 部分 OpenAI-compatible 厂商只在 HTTP 200 的
                            # SSE error frame 里报业务错误 —— 中止流并抛出。
                            raise ev.error
                        yield ev
            except httpx.HTTPError as exc:
                raise self.normalize_error(exc=exc) from exc

        acc.elapsed_ms = int((time.perf_counter() - start) * 1000)
        yield StreamEvent(kind="done", response=acc.to_response())


# ---------------------------------------------------------------------------
# Shared connection pool
# ---------------------------------------------------------------------------

# One httpx.Client per request origin (scheme+host+port), shared by every
# provider adapter and call site (chat / stream / health / warm-up). httpx
# clients are thread-safe and keep-alive their connections, so consecutive
# turns to the same vendor reuse the TLS session instead of re-handshaking.
# Keyed by origin — never by API key — because connections are not
# authenticated state; the Authorization header travels per request.
_CLIENT_POOL: dict[tuple[str, str, int], httpx.Client] = {}
_CLIENT_POOL_LOCK = threading.Lock()
# Generous idle keep-alive: REPL turns can be minutes apart, and a closed
# idle connection is silently re-established by httpx anyway.
_POOL_TIMEOUTS = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
_POOL_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16)


def _shared_http_client(url: str) -> httpx.Client:
    try:
        u = httpx.URL(url)
        key = (u.scheme, u.host, u.port or (443 if u.scheme == "https" else 80))
    except Exception:
        key = ("", url, 0)
    with _CLIENT_POOL_LOCK:
        client = _CLIENT_POOL.get(key)
        if client is None:
            client = httpx.Client(timeout=_POOL_TIMEOUTS, limits=_POOL_LIMITS)
            _CLIENT_POOL[key] = client
        return client


def close_shared_http_clients() -> None:
    """Tear the pool down (tests / embedding callers); the REPL never calls this."""
    with _CLIENT_POOL_LOCK:
        clients = list(_CLIENT_POOL.values())
        _CLIENT_POOL.clear()
    for c in clients:
        try:
            c.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Stream event types + accumulator
# ---------------------------------------------------------------------------

@dataclass
class StreamEvent:
    """One delta from :meth:`HTTPProvider.stream_chat`.

    ``kind`` ∈ ``content`` | ``reasoning`` | ``tool_delta`` | ``finish``
    | ``usage`` | ``done``.

    * ``content`` / ``reasoning`` carry a non-empty ``text``.
    * ``tool_delta`` is emitted as the model builds each tool_call; the
      final state lives on the ``done`` event's ``response.tool_calls``.
    * ``finish`` carries ``finish_reason``.
    * ``usage`` carries the provider's usage dict (may be missing).
    * ``done`` carries the assembled :class:`ChatResponse`.
    """

    kind: str
    text: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    response: ChatResponse | None = None
    # kind == "error" 时携带已分类的 ProviderError（U10：HTTP 200 的 SSE
    # 业务错误帧），由 stream_chat 中止流并抛出。
    error: ProviderError | None = None


@dataclass
class _StreamAccumulator:
    """Reassemble OpenAI-streaming chunks into a normalised response."""

    provider: str
    model_default: str
    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    model_id: str | None = None
    raw_chunks: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0

    def consume(self, chunk: dict[str, Any]) -> list[StreamEvent]:
        self.raw_chunks.append(chunk)
        events: list[StreamEvent] = []
        if "model" in chunk and isinstance(chunk["model"], str):
            self.model_id = chunk["model"]

        # U10: HTTP 200 的 SSE 业务错误帧（无 choices）——识别后以 kind=
        # "error" 事件上抛（stream_chat 中止流并抛 ProviderError），不再
        # 静默忽略导致流被"正常"结束成空响应。
        biz = detect_business_error(chunk)
        if biz is not None:
            events.append(StreamEvent(
                kind="error",
                text=biz.get("message") or "业务错误",
                error=provider_error_from_business(biz, provider=self.provider),
            ))
            return events

        # OpenAI emits a trailing chunk with usage and empty choices.
        u = chunk.get("usage")
        if isinstance(u, dict) and u:
            self.usage = u
            events.append(StreamEvent(kind="usage", usage=u))

        choices = chunk.get("choices") or []
        if not isinstance(choices, list) or not choices:
            return events
        choice = choices[0]
        if not isinstance(choice, dict):
            # Tolerate a malformed SSE chunk (``{"choices": [null]}`` etc.)
            # the same way non-dict tool-call entries are skipped below,
            # instead of crashing the stream with an AttributeError.
            return events
        delta = choice.get("delta") or {}

        if isinstance(delta.get("content"), str) and delta["content"]:
            self.content_parts.append(delta["content"])
            events.append(StreamEvent(kind="content", text=delta["content"]))

        # DeepSeek-reasoner / Kimi-thinking / MiMo / Qwen-thinking stream
        # this in parallel to content. We MUST keep it on the assistant
        # message we save back to the session.
        rc = delta.get("reasoning_content")
        if isinstance(rc, str) and rc:
            self.reasoning_parts.append(rc)
            events.append(StreamEvent(kind="reasoning", text=rc))

        tcs = delta.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                try:
                    idx = int(tc.get("index", 0))
                except (TypeError, ValueError):
                    idx = 0
                slot = self.tool_calls.setdefault(
                    idx,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if tid := tc.get("id"):
                    slot["id"] = (slot["id"] or "") + tid
                if ttype := tc.get("type"):
                    slot["type"] = ttype
                fn = tc.get("function") or {}
                if name := fn.get("name"):
                    slot["function"]["name"] = (slot["function"]["name"] or "") + name
                if args := fn.get("arguments"):
                    slot["function"]["arguments"] = (slot["function"]["arguments"] or "") + args
            events.append(StreamEvent(kind="tool_delta"))

        fr = choice.get("finish_reason")
        if fr:
            self.finish_reason = fr
            events.append(StreamEvent(kind="finish", finish_reason=fr))

        return events

    def to_response(self) -> ChatResponse:
        content = "".join(self.content_parts)
        reasoning = "".join(self.reasoning_parts) or None
        tool_calls = (
            [v for _, v in sorted(self.tool_calls.items())]
            if self.tool_calls
            else None
        )
        raw_message: dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning is not None:
            raw_message["reasoning_content"] = reasoning
        if tool_calls is not None:
            raw_message["tool_calls"] = tool_calls
        return ChatResponse(
            content=content,
            reasoning_content=reasoning,
            tool_calls=tool_calls,
            raw={"chunks": len(self.raw_chunks), "raw_chunks": self.raw_chunks},
            raw_message=raw_message,
            usage=self.usage,
            model=self.model_id or self.model_default,
            provider=self.provider,
            finish_reason=self.finish_reason,
            elapsed_ms=self.elapsed_ms,
        )
