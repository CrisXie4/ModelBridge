"""Unified request / response schemas shared by all providers.

Keeping a single canonical shape means the CLI, the (future) Agent layer,
and the (future) Web Server / Proxy all speak the same language — provider
adapters translate to/from this on the wire.

Special attention to ``reasoning_content`` and ``raw`` on
:class:`ChatMessage` and :class:`ChatResponse`: MiMo, Kimi-thinking, and
DeepSeek-reasoner break if those fields are dropped on a follow-up turn.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """A single message in a chat conversation.

    Optional fields are kept as ``None`` rather than empty strings so providers
    can decide what to drop. ``raw`` lets adapters stash the exact wire form
    of an assistant turn — critical for MiMo's reasoning_content invariant.
    """

    model_config = ConfigDict(extra="allow")

    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    raw: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        """Render this message for an OpenAI-style request body.

        Provider adapters may override this (or post-process the payload),
        but the default keeps every field a provider could plausibly need.
        """
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.name is not None:
            msg["name"] = self.name
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.reasoning_content is not None:
            msg["reasoning_content"] = self.reasoning_content
        return msg


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Provider-agnostic chat completion request."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    response_format: Any | None = None

    # Cross-provider knobs. Adapters translate these per provider:
    #   Qwen   → extra_body.enable_thinking / thinking_budget
    #   Kimi   → server-side via model name (kimi-k1 vs kimi-k1-thinking)
    #   DeepSeek → server-side via model name (deepseek-reasoner)
    #   MiMo   → server-side
    thinking: bool | None = None
    thinking_budget: int | None = None

    # Anything provider-specific that the user (or upstream caller) wants to
    # pass through verbatim. Adapters can override / merge keys here.
    extra_body: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class ChatResponse(BaseModel):
    """Normalised chat completion response.

    ``raw`` is *always* set to the full JSON-decoded provider response so
    higher layers (Agent loop, MCP bridge, raw logger) can inspect anything
    the adapter didn't surface.
    """

    model_config = ConfigDict(extra="allow")

    content: str = ""
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    raw_message: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None
    model: str | None = None
    provider: str | None = None
    finish_reason: str | None = None
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

class ModelCapability(BaseModel):
    """Capability matrix for a model.

    This intentionally mirrors :class:`modelbridge.models.Capabilities` but
    sits in the provider layer where adapters consume it. v0.2 adds
    ``streaming`` (used by future server / agent stages).
    """

    tools: bool = False
    # ``json`` intentionally shadows the deprecated pydantic ``BaseModel.json()``
    # method — it's a capability flag exposed in configs as ``capabilities.json``.
    json: bool = False  # type: ignore[assignment]
    vision: bool = False
    reasoning: bool = False
    reasoning_content_back: bool = False
    cache: bool = False
    local: bool = False
    streaming: bool = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """Unified provider error.

    Carries enough structure for the CLI to render a friendly, *actionable*
    diagnostic — not just a stack trace. See :mod:`modelbridge.error_hints`
    for hint generation.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        hint: str | None = None,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code
        # one of: http_4xx, http_5xx, timeout, connect, decode, transport, unknown
        self.error_type = error_type
        self.hint = hint
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status_code": self.status_code,
            "error_type": self.error_type,
            "message": self.message,
            "hint": self.hint,
        }


__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ModelCapability",
    "ProviderError",
]
