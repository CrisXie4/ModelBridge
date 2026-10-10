"""Conversation history that survives across REPL turns and tool calls.

The session is the *single source of truth* for what the model has seen.
Every assistant turn is appended raw (preserving ``reasoning_content`` +
``tool_calls`` for MiMo / Kimi-thinking / DeepSeek-reasoner compat); every
tool result is appended as ``role=tool``.

Sessions can be persisted to ``~/.modelbridge/sessions/`` and restored:
:meth:`Session.to_dict` writes them, :func:`load_session` /
:meth:`Session.from_dict` read them back (``mbridge resume`` / REPL
``/resume``, U12). Restore degrades to a fresh session on any corruption.
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


def load_session(path: Path | str) -> Session | None:
    """从 ``to_dict`` 写出的 JSON 文件恢复会话（U12：``mbridge resume`` /
    REPL ``/resume`` 的入口）。

    文件不存在 / 不是 JSON / 结构损坏 → 返回 ``None``，调用方降级为新会话；
    永不抛出。单条消息损坏则跳过该条，不拖垮整个会话。
    """
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Session.from_dict(data)
    except Exception:  # noqa: BLE001 — 恢复失败一律降级，绝不抛
        return None


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

    def add_tool_result(
        self, *, tool_call_id: str, content: str, error: bool = False
    ) -> None:
        """追加工具结果（``error`` 为真表示工具失败 —— 记录在消息上供
        microcompact 保护失败结果，协议 wire 不变）。"""
        self.messages.append(
            ChatMessage(
                role="tool",
                tool_call_id=tool_call_id,
                content=content,
                error=error,
            )
        )

    # ------------------------------------------------------------------
    # 真实用量锚点（E4/E5）
    # ------------------------------------------------------------------

    def note_usage(self, usage: dict[str, Any] | None) -> None:
        """记录最近一次真实用量（``resp.usage`` → metadata 锚点）。

        压缩决策与 max_tokens 收缩都以锚点优先（provider 权威值），无锚点
        回退字符估算。非数值/缺字段静默忽略（不影响主流程）。
        """
        if not isinstance(usage, dict):
            return
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if not isinstance(prompt, (int, float)) or not isinstance(
            completion, (int, float)
        ):
            return
        self.metadata["usage_anchor"] = {
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
        }

    def anchored_usage_tokens(self) -> int | None:
        """锚点用量 = 最近一次请求的 prompt+completion tokens；无锚点 None。"""
        anchor = self.metadata.get("usage_anchor")
        if not isinstance(anchor, dict):
            return None
        prompt = anchor.get("prompt_tokens")
        completion = anchor.get("completion_tokens")
        if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
            return int(prompt) + int(completion)
        return None

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """``to_dict`` 的逆（U12 会话恢复）：还原 model / created_at /
        metadata 与完整消息历史（``tool_calls`` / ``reasoning_content`` 都在
        ``model_dump`` 里，往返无损）。缺键按默认值兜底；非法消息条目跳过。
        """
        session = cls(model_name=str(data.get("model") or "resumed"))
        created = data.get("created_at")
        if isinstance(created, str) and created:
            session.created_at = created
        meta = data.get("metadata")
        if isinstance(meta, dict):
            session.metadata = dict(meta)
        messages = data.get("messages")
        if isinstance(messages, list):
            restored: list[ChatMessage] = []
            for m in messages:
                if not isinstance(m, dict):
                    continue
                try:
                    restored.append(ChatMessage.model_validate(m))
                except Exception:  # noqa: BLE001 — 单条损坏跳过，不拖垮会话
                    continue
            session.messages = restored
        return session

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


__all__ = ["Session", "get_sessions_dir", "load_session"]
