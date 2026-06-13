"""M7 — answer servers' ``sampling/createMessage`` with our configured models.

MCP sampling lets a *server* borrow the *client's* LLM: it sends a
``sampling/createMessage`` request with chat-style messages, and we reply
with a completion from a model the user already configured in ModelBridge —
the server never sees an API key.

Security posture: **off by default**. The user opts in via::

    mcp:
      sampling:
        enabled: true
        model: deepseek-v3      # optional; default_model otherwise
        max_tokens: 2048        # hard cap, whatever the server asks for

The handler is synchronous (called from the session's RPC wait loop) and
must never raise upward — :meth:`MCPClientSession._handle_server_request`
wraps it and converts exceptions into a JSON-RPC error reply.
"""

from __future__ import annotations

from typing import Any

from ..client import get_model_entry, resolve_model_name
from ..providers import get_provider
from ..schemas import ChatMessage, ChatRequest
from .config import MCPSettings
from .logging import log_lifecycle
from .session.client_session import SamplingHandler


def _flatten_content(content: Any) -> str:
    """MCP message content (object or list of blocks) → plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return ""
    if isinstance(content, list):
        return "\n".join(_flatten_content(c) for c in content)
    return ""


def build_sampling_handler(settings: MCPSettings) -> SamplingHandler:
    """Build the callable that turns sampling params into a model completion."""

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        messages: list[ChatMessage] = []
        system = params.get("systemPrompt")
        if isinstance(system, str) and system.strip():
            messages.append(ChatMessage(role="system", content=system))
        for m in params.get("messages") or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "user")
            if role not in ("user", "assistant"):
                role = "user"
            text = _flatten_content(m.get("content"))
            messages.append(ChatMessage(role=role, content=text))
        if not any(m.role != "system" for m in messages):
            raise ValueError("sampling/createMessage 缺少 messages")

        asked = params.get("maxTokens")
        max_tokens = settings.sampling_max_tokens
        if isinstance(asked, int) and asked > 0:
            max_tokens = min(asked, settings.sampling_max_tokens)

        name = resolve_model_name(settings.sampling_model)
        entry = get_model_entry(name)
        request = ChatRequest(model=entry.model, messages=messages, max_tokens=max_tokens)
        provider = get_provider(entry)
        resp = provider.chat(request, timeout=120.0, verbose_label="mcp_sampling")

        log_lifecycle("*", "sampling_served",
                      f"model={entry.model} out_chars={len(resp.content or '')}")
        return {
            "role": "assistant",
            "content": {"type": "text", "text": resp.content or ""},
            "model": resp.model or entry.model,
            "stopReason": "endTurn",
        }

    return handler


__all__ = ["build_sampling_handler"]
