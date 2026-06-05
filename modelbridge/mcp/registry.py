"""Glue: register a manager's catalog tools into an agent ``ToolRegistry``.

This is the one call the CLI makes to light up MCP for the agent loop. It wraps
each catalog tool in an :class:`MCPToolAdapter` and registers it, skipping any
server whose ``tool_policy`` is ``deny`` (discovery-only).
"""

from __future__ import annotations

from ..agent.tools.registry import ToolRegistry
from .adapters.tool_adapter import MCPToolAdapter
from .config import ToolPolicyKind
from .manager.manager import MCPManager


def register_mcp_tools(registry: ToolRegistry, manager: MCPManager) -> int:
    """Register every (non-denied) catalog tool. Returns the count registered."""
    count = 0
    for qt in manager.catalog.tools:
        policy = manager.policy_for(qt.server_id)
        if policy == ToolPolicyKind.DENY:
            continue
        adapter = MCPToolAdapter(
            manager=manager,
            server_id=qt.server_id,
            qualified_name=qt.qualified_name,
            tool=qt.tool,
            policy=policy,
        )
        registry.register(adapter)
        count += 1
    return count


__all__ = ["register_mcp_tools"]
