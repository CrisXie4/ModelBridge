"""Glue: register a manager's catalog tools into an agent ``ToolRegistry``.

This is the one call the CLI makes to light up MCP for the agent loop. It
wraps each catalog tool in an :class:`MCPToolAdapter` and registers it,
skipping any tool whose effective policy is ``deny`` (server ``tool_policy``
or per-tool ``tool_overrides``, M6).

:func:`sync_mcp_tools` (M5/M6) reconciles the registry after the catalog
changes at runtime — server reconnect, ``list_changed`` hot refresh, or
``/mcp on|off`` — adding new adapters and dropping stale ones while leaving
non-MCP tools untouched.
"""

from __future__ import annotations

from ..agent.tools.registry import ToolRegistry
from .adapters.tool_adapter import MCPToolAdapter
from .config import ToolPolicyKind
from .manager.manager import MCPManager


def _desired_adapters(manager: MCPManager) -> dict[str, MCPToolAdapter]:
    """qualified_name → adapter for every tool that should be registered now."""
    out: dict[str, MCPToolAdapter] = {}
    for qt in manager.catalog.tools:
        if manager.is_runtime_disabled(qt.server_id):
            continue
        policy = manager.policy_for_tool(qt.server_id, qt.tool.name)
        if policy == ToolPolicyKind.DENY:
            continue
        out[qt.qualified_name] = MCPToolAdapter(
            manager=manager,
            server_id=qt.server_id,
            qualified_name=qt.qualified_name,
            tool=qt.tool,
            policy=policy,
        )
    return out


def register_mcp_tools(registry: ToolRegistry, manager: MCPManager) -> int:
    """Register every (non-denied) catalog tool. Returns the count registered.

    Also binds the registry to the manager so later catalog changes
    (reconnect / list_changed / ``/mcp on|off``) re-sync automatically.
    """
    desired = _desired_adapters(manager)
    for adapter in desired.values():
        registry.register(adapter)
    manager.bind_registry(registry)
    return len(desired)


def sync_mcp_tools(registry: ToolRegistry, manager: MCPManager) -> tuple[int, int]:
    """Reconcile MCP adapters in ``registry`` with the current catalog.

    Returns ``(added, removed)``. Non-MCP tools are never touched.
    """
    desired = _desired_adapters(manager)
    current = {
        name for name, tool in list(registry.tools.items())
        if isinstance(tool, MCPToolAdapter)
    }
    removed = 0
    for name in current - set(desired):
        registry.unregister(name)
        removed += 1
    added = 0
    for name, adapter in desired.items():
        # Always (re)register: after a hot refresh the tool's schema or
        # description may have changed even when the name didn't.
        registry.register(adapter)
        if name not in current:
            added += 1
    return added, removed


__all__ = ["register_mcp_tools", "sync_mcp_tools"]
