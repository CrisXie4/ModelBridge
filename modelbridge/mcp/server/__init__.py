"""M7 — ModelBridge as an MCP *server* (the reverse direction).

``mbridge mcp serve`` (or ``python -m modelbridge.mcp.server``) speaks MCP
over stdio and exposes ModelBridge's own capabilities — chat with any
configured国产 model, model listing, and task routing — to MCP hosts like
Claude Desktop / Cursor / another ModelBridge.
"""

from .builtin import build_modelbridge_server
from .server import MCPServer, ServerTool

__all__ = ["MCPServer", "ServerTool", "build_modelbridge_server"]
