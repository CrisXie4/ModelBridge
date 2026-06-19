"""Tool ABC and data classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..context import AgentContext


@dataclass
class ToolCall:
    """A single tool invocation parsed off a model response."""

    id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """The string the agent loop will hand back to the model in ``role=tool``."""

    content: str
    is_error: bool = False
    # Optional structured output kept for the session log; not sent on the wire.
    structured: dict[str, Any] | None = None
    # Extra messages the loop appends to the session *after* the tool result.
    # Used by ``view_image`` to inject an image as a user message (OpenAI-style
    # role=tool content can't carry images). Typed loosely to avoid importing
    # ChatMessage into the tool layer.
    extra_messages: list[Any] | None = None

    def to_tool_message(self, *, tool_call_id: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": self.content,
        }


class Tool(ABC):
    """Abstract tool. Subclasses set ``name`` / ``description`` and implement ``execute``."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def json_schema(self) -> dict[str, Any]:
        """Return the ``function.parameters`` JSON schema."""

    def openai_tool(self) -> dict[str, Any]:
        """Render this tool as an entry for ``ChatRequest.tools``."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema(),
            },
        }

    @abstractmethod
    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult: ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def err(message: str, *, hint: str | None = None) -> ToolResult:
        body = message if not hint else f"{message}\n\n提示: {hint}"
        return ToolResult(content=body, is_error=True)

    @staticmethod
    def ok(content: str, *, structured: dict[str, Any] | None = None) -> ToolResult:
        return ToolResult(content=content, is_error=False, structured=structured)
