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
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from ..error_hints import (
    classify_error_type,
    hint_for_exception,
    hint_for_http_error,
)
from ..models import ModelEntry, ProviderType
from ..raw_logger import save_raw_exchange
from ..schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ModelCapability,
    ProviderError,
)
from ..secrets import reveal
from ..utils import resolve_api_key


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
    # Capability helpers
    # ------------------------------------------------------------------

    def supports_capability(self, cap: str) -> bool:
        return bool(getattr(self.entry.capabilities, cap, False))

    def get_capabilities(self) -> ModelCapability:
        c = self.entry.capabilities
        return ModelCapability(
            tools=c.tools,
            json=c.json,
            vision=c.vision,
            reasoning=c.reasoning,
            reasoning_content_back=c.reasoning_content_back,
            cache=c.cache,
            local=c.local,
            streaming=getattr(c, "streaming", False),
        )

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
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{url}/models", headers=self.build_headers())
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
        return body

    def _serialize_message(self, m: ChatMessage) -> dict[str, Any]:
        # MiMo / DeepSeek-reasoner / Kimi-thinking REQUIRE reasoning_content
        # to survive on assistant turns that contain tool_calls. Default
        # keeps it — adapters with stricter servers can override.
        return m.to_wire()

    def parse_chat_response(self, data: dict[str, Any]) -> ChatResponse:
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
        return ProviderError(
            f"{sc} {msg}",
            provider=self.name,
            status_code=sc,
            error_type=classify_error_type(sc),
            hint=hint_for_http_error(
                sc, provider=self.name, body=body, model=self.entry.name
            ),
            raw=body,
        )

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
                m = err.get("message")
                if isinstance(m, str):
                    return m
            m = j.get("message")
            if isinstance(m, str):
                return m
        return None

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

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
        endpoint = self.chat_endpoint()
        headers = self.build_headers()
        body = self.build_chat_payload(request)

        start = time.perf_counter()
        response_status: int | None = None
        response_raw: Any = None
        error_dict: dict[str, Any] | None = None
        try:
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(endpoint, headers=headers, json=body)
            except httpx.HTTPError as exc:
                err = self.normalize_error(exc=exc)
                error_dict = err.to_dict()
                raise err from exc

            response_status = resp.status_code
            text = resp.text

            if resp.status_code >= 400:
                err = self.normalize_error(status_code=resp.status_code, body=text)
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

        try:
            client = httpx.Client(timeout=timeout)
        except httpx.HTTPError as exc:
            raise self.normalize_error(exc=exc) from exc

        try:
            try:
                stream_cm = client.stream("POST", endpoint, headers=headers, json=body)
            except httpx.HTTPError as exc:
                raise self.normalize_error(exc=exc) from exc

            with stream_cm as resp:
                if resp.status_code >= 400:
                    body_text = resp.read().decode("utf-8", errors="replace")
                    raise self.normalize_error(status_code=resp.status_code, body=body_text)

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
                            yield ev
                except httpx.HTTPError as exc:
                    raise self.normalize_error(exc=exc) from exc

        finally:
            client.close()

        acc.elapsed_ms = int((time.perf_counter() - start) * 1000)
        yield StreamEvent(kind="done", response=acc.to_response())


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
