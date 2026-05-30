"""Tool registry + dispatch helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..context import AgentContext
from .base import Tool, ToolCall, ToolResult
from .bash_tool import RunBashTool
from .file_tools import ListDirTool, ReadFileTool, StrReplaceTool, WriteFileTool


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("tool.name must be set")
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return sorted(self.tools)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [t.openai_tool() for t in self.tools.values()]

    def dispatch(self, call: ToolCall, ctx: AgentContext) -> ToolResult:
        tool = self.get(call.name)
        if tool is None:
            return ToolResult(
                content=f"未知 tool: {call.name}。已注册: {', '.join(self.names())}",
                is_error=True,
            )
        try:
            return tool.execute(call.arguments, ctx)
        except Exception as e:  # noqa: BLE001 — tool boundary
            # Surface tool errors back to the model so it can react,
            # rather than crashing the loop.
            return ToolResult(
                content=f"tool {call.name} 抛出 {type(e).__name__}: {e}",
                is_error=True,
            )


def build_default_registry(*, include_bash: bool = False) -> ToolRegistry:
    """Default toolset: read/list always on; write/edit always on; bash opt-in.

    ``include_bash=False`` *also* removes the tool from the schema list sent
    to the model — keeping the model from even attempting it when disabled.
    """
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(ListDirTool())
    reg.register(WriteFileTool())
    reg.register(StrReplaceTool())
    if include_bash:
        reg.register(RunBashTool())
    return reg


def parse_tool_calls(raw_tool_calls: list[dict[str, Any]] | None) -> list[ToolCall]:
    """Parse the ``tool_calls`` array from a provider response into :class:`ToolCall`."""
    if not raw_tool_calls:
        return []
    parsed: list[ToolCall] = []
    for entry in raw_tool_calls:
        if not isinstance(entry, dict):
            continue
        call_id = str(entry.get("id") or "")
        fn = entry.get("function") or {}
        name = str(fn.get("name") or "")
        raw_args = fn.get("arguments")
        args: dict[str, Any]
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": raw_args, "_parse_error": True}
        else:
            args = {}
        if not isinstance(args, dict):
            args = {"_raw": args}
        parsed.append(ToolCall(id=call_id, name=name, arguments=args, raw=entry))
    return parsed
