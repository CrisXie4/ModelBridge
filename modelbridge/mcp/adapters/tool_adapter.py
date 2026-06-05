"""MCPToolAdapter — expose a remote MCP tool as a native agent ``Tool``.

This is the load-bearing bridge: it subclasses the existing
:class:`modelbridge.agent.tools.base.Tool`, so once registered in a
``ToolRegistry`` the agent loop dispatches to it identically to a built-in
tool — zero loop changes.

Two guarantees inherited from the project's conventions:

* **Error boundary** — every :class:`MCPError` (and any stray exception) is
  caught and returned as ``ToolResult(is_error=True)``; the loop never crashes
  (mirrors ``ToolRegistry.dispatch``).
* **Approval gate** — under ``tool_policy: approve`` the call goes through
  ``AgentContext.confirm`` (the same y/N/always prompt as bash/write tools)
  before any bytes hit the server.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ...agent.context import AgentContext
from ...agent.tools.base import Tool, ToolResult
from ..config import ToolPolicyKind
from ..errors import MCPError, MCPSecurityError
from ..logging import log_tool_call
from ..protocol.types import MCPTool

if TYPE_CHECKING:
    from ..manager.manager import MCPManager

# Cap the text we hand back to the model so a chatty tool can't blow the
# context window. The full result is still logged.
_MAX_RESULT_CHARS = 16000


class MCPToolAdapter(Tool):
    def __init__(
        self,
        *,
        manager: "MCPManager",
        server_id: str,
        qualified_name: str,
        tool: MCPTool,
        policy: ToolPolicyKind,
    ) -> None:
        self._manager = manager
        self._server_id = server_id
        self._tool = tool
        self.name = qualified_name
        # Tag the origin so the model (and logs) know where a tool comes from.
        base_desc = tool.description or f"MCP tool {tool.name}"
        self.description = f"[MCP:{server_id}] {base_desc}"
        self._policy = policy

    # ------------------------------------------------------------------
    def json_schema(self) -> dict[str, Any]:
        schema = self._tool.input_schema or {}
        # OpenAI requires an object schema; default to a permissive one.
        if not isinstance(schema, dict) or schema.get("type") != "object":
            return {"type": "object", "properties": {}}
        return schema

    # ------------------------------------------------------------------
    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        if self._policy == ToolPolicyKind.DENY:
            return Tool.err(
                f"MCP tool {self.name} 已被策略禁用 (tool_policy: deny)",
                hint="如需启用，把该 server 的 tool_policy 改为 approve 或 auto",
            )

        if self._policy == ToolPolicyKind.APPROVE:
            preview = self._arg_preview(args)
            approved = ctx.confirm(
                tool=self.name,
                summary=f"调用 MCP 工具 {self._tool.name} @ {self._server_id}",
                detail=preview,
            )
            if not approved:
                return Tool.err(f"用户拒绝了对 {self.name} 的调用")

        started = time.monotonic()
        try:
            result = self._manager.call_tool(self.name, args)
        except MCPSecurityError as e:
            return Tool.err(e.message, hint=e.hint)
        except MCPError as e:
            return Tool.err(e.display())
        except Exception as e:  # noqa: BLE001 — tool boundary, never crash the loop
            return Tool.err(f"MCP tool {self.name} 抛出 {type(e).__name__}: {e}")

        elapsed_ms = int((time.monotonic() - started) * 1000)
        text = result.joined_text()
        log_tool_call(
            server_id=self._server_id,
            tool=self._tool.name,
            correlation_id=self.name,
            elapsed_ms=elapsed_ms,
            is_error=result.is_error,
            result_chars=len(text),
        )

        if len(text) > _MAX_RESULT_CHARS:
            text = text[:_MAX_RESULT_CHARS] + f"\n…[已截断，共 {len(text)} 字符]"

        if result.is_error:
            return ToolResult(content=text or "(MCP tool 返回错误，无内容)",
                              is_error=True, structured=result.structured)
        return ToolResult(content=text or "(MCP tool 无输出)",
                          is_error=False, structured=result.structured)

    # ------------------------------------------------------------------
    @staticmethod
    def _arg_preview(args: dict[str, Any], *, limit: int = 400) -> str:
        import json

        try:
            s = json.dumps(args, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            s = str(args)
        return s if len(s) <= limit else s[:limit] + " …"


__all__ = ["MCPToolAdapter"]
