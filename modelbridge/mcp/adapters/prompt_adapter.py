"""Turn an MCP prompt into ModelBridge chat messages.

``prompts/get`` returns a list of role-tagged messages. We map them onto the
project's :class:`modelbridge.schemas.ChatMessage` so a fetched prompt can be
seeded straight into a :class:`Session` as the start of a turn.

MCP prompt roles are ``user`` / ``assistant``; we pass them through and treat
anything else as ``user`` defensively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...schemas import ChatMessage

if TYPE_CHECKING:
    from ..manager.manager import MCPManager

_VALID_ROLES = {"user", "assistant", "system"}


class MCPPromptAdapter:
    def __init__(self, manager: "MCPManager") -> None:
        self._manager = manager

    def to_messages(
        self, qualified_name: str, arguments: dict[str, Any] | None = None
    ) -> list[ChatMessage]:
        result = self._manager.get_prompt(qualified_name, arguments)
        messages: list[ChatMessage] = []
        for m in result.messages:
            role = m.role if m.role in _VALID_ROLES else "user"
            messages.append(ChatMessage(role=role, content=m.content))
        return messages

    def describe(self, qualified_name: str, arguments: dict[str, Any] | None = None) -> str:
        result = self._manager.get_prompt(qualified_name, arguments)
        return result.description


__all__ = ["MCPPromptAdapter"]
