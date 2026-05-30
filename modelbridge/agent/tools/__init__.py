"""Agent tools.

Each tool is a subclass of :class:`Tool` with:

* ``name`` — function name exposed to the model
* ``description`` — what it does (becomes the function description)
* ``json_schema()`` — OpenAI function-tool JSON schema
* ``execute(args, ctx)`` — returns :class:`ToolResult`

The registry assembles the tool list for :class:`ChatRequest` and
dispatches incoming ``tool_calls`` from model responses.
"""

from .base import Tool, ToolCall, ToolResult
from .registry import (
    ToolRegistry,
    build_default_registry,
    parse_tool_calls,
)

__all__ = [
    "Tool",
    "ToolCall",
    "ToolResult",
    "ToolRegistry",
    "build_default_registry",
    "parse_tool_calls",
]
