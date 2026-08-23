"""Conversation history that survives across REPL turns and tool calls.

The session is the *single source of truth* for what the model has seen.
Every assistant turn is appended raw (preserving ``reasoning_content`` +
``tool_calls`` for MiMo / Kimi-thinking / DeepSeek-reasoner compat); every
tool result is appended as ``role=tool``.

Sessions can be persisted to ``~/.modelbridge/sessions/`` so a debugging
user can replay or reuse them. v0.3 only writes them; restore is left
for later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import ChatMessage, ChatResponse
from ..utils import get_app_dir, now_filestamp, now_iso


SESSIONS_DIR_NAME = "sessions"

#: Wire keys ``ChatMessage.to_wire()`` already emits. ``raw_message`` from a
#: provider echoes these back verbatim; keeping them in ``raw`` would double
#: every assistant turn's memory (content + tool_calls + reasoning_content
#: all duplicated) for the whole session lifetime.
_WIRE_KEYS = frozenset(
    {"role", "content", "name", "tool_calls", "tool_call_id", "reasoning_content"}
)


def _raw_delta(raw_message: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce a provider ``raw_message`` to the keys ``to_wire()`` misses.

    The only consumer of ``ChatMessage.raw`` (the MiMo adapter) merges keys
    that ``to_wire()`` didn't emit, so the delta preserves its behaviour
    exactly while dropping the duplicated bulk.
    """
    if not isinstance(raw_message, dict):
        return None
    delta = {
        k: v for k, v in raw_message.items()
        if k not in _WIRE_KEYS and v is not None
    }
    return delta or None


def get_sessions_dir() -> Path:
    return get_app_dir() / SESSIONS_DIR_NAME


@dataclass
class Session:
    """Live history for one agent run."""

    model_name: str
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Building history
    # ------------------------------------------------------------------

    def add_system(self, content: str) -> None:
        # System messages live at the front of the list to keep them sticky.
        self.messages.insert(0, ChatMessage(role="system", content=content))

    def add_user(self, content: str, images: list[dict] | None = None) -> None:
        if images:
            # Multimodal turn: text first, then image_url blocks — one message
            # the vision model reads as "picture + question together".
            blocks = [{"type": "text", "text": content}, *images]
            self.messages.append(ChatMessage(role="user", content=blocks))
        else:
            self.messages.append(ChatMessage(role="user", content=content))

    def add_assistant(self, resp: ChatResponse) -> None:
        """Append the assistant turn, preserving every field a provider needs.

        Why we keep ``reasoning_content`` and the raw message:

        * MiMo (thinking + tool_calls) requires the **exact same**
          ``reasoning_content`` to come back on the next turn.
        * Kimi-thinking / DeepSeek-reasoner have similar invariants.
        * Our adapters serialise via :meth:`ChatMessage.to_wire` which
          emits these fields when present — so we just need to keep them.
        """
        msg = ChatMessage(
            role="assistant",
            content=resp.content or "",
            tool_calls=resp.tool_calls,
            reasoning_content=resp.reasoning_content,
            raw=_raw_delta(resp.raw_message),
        )
        self.messages.append(msg)

    def add_tool_result(self, *, tool_call_id: str, content: str) -> None:
        self.messages.append(
            ChatMessage(role="tool", tool_call_id=tool_call_id, content=content)
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "messages": [m.model_dump(mode="json") for m in self.messages],
        }

    def save(self, *, label: str = "agent") -> Path | None:
        """Persist to ``~/.modelbridge/sessions/<ts>_<label>.json``.

        Returns the path on success, or ``None`` if the disk wasn't
        writable (we never want telemetry to crash the agent).
        """
        try:
            sd = get_sessions_dir()
            sd.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        path = sd / f"{now_filestamp()}_{label}.json"
        try:
            path.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            return None
        return path


__all__ = ["Session", "get_sessions_dir"]
