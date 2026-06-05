"""ModelBridge MCP module — act as an MCP client across multiple servers.

Public facade. Typical wiring from the CLI / agent REPL::

    from modelbridge.mcp import MCPManager, register_mcp_tools

    manager = MCPManager.from_config(verbose=verbose)
    manager.connect_all()
    register_mcp_tools(registry, manager)
    ...
    manager.shutdown()

See ``docs/mcp-architecture.md`` for the full design. This milestone covers
M0–M3: stdio transport, handshake + discovery, tool calls, resources, and
prompts, with multi-server governance and failure isolation.
"""

from __future__ import annotations

from .adapters import MCPPromptAdapter, MCPResourceProvider, MCPToolAdapter
from .config import (
    MCPServerConfig,
    MCPSettings,
    ToolPolicyKind,
    TransportKind,
    load_mcp_settings,
)
from .errors import (
    MCPCapabilityError,
    MCPConfigError,
    MCPConnectError,
    MCPError,
    MCPProtocolError,
    MCPSecurityError,
    MCPTimeoutError,
    MCPToolError,
    MCPTransportError,
    MCPVersionMismatch,
)
from .manager import Catalog, MCPManager, ServerStatus
from .registry import register_mcp_tools
from .session import MCPClientSession, SessionState


def is_enabled() -> bool:
    """True when the user has configured and enabled MCP in config.yaml."""
    settings = load_mcp_settings()
    return settings.enabled and bool(settings.enabled_servers())


__all__ = [
    # facade
    "MCPManager",
    "register_mcp_tools",
    "is_enabled",
    # config
    "MCPServerConfig",
    "MCPSettings",
    "TransportKind",
    "ToolPolicyKind",
    "load_mcp_settings",
    # manager / session
    "Catalog",
    "ServerStatus",
    "MCPClientSession",
    "SessionState",
    # adapters
    "MCPToolAdapter",
    "MCPResourceProvider",
    "MCPPromptAdapter",
    # errors
    "MCPError",
    "MCPConfigError",
    "MCPConnectError",
    "MCPTransportError",
    "MCPTimeoutError",
    "MCPProtocolError",
    "MCPVersionMismatch",
    "MCPCapabilityError",
    "MCPToolError",
    "MCPSecurityError",
]
