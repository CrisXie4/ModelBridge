"""MCP exception hierarchy.

Mirrors the project's "exception + actionable hint" style (see
:class:`modelbridge.config.ConfigError`,
:class:`modelbridge.agent.security.PathDenied`,
:class:`modelbridge.schemas.ProviderError`) but stays a **separate** tree:
those are model-call / filesystem errors; these are MCP transport / protocol
/ tool errors with different recovery advice.

Every error carries an optional ``server_id`` (which server it came from) and
a Chinese ``hint`` (what the user should do about it). The error boundary in
:class:`modelbridge.mcp.adapters.tool_adapter.MCPToolAdapter` catches the whole
tree and turns it into a ``ToolResult(is_error=True)`` so the agent loop never
crashes — the same philosophy as ``ToolRegistry.dispatch``.
"""

from __future__ import annotations

from typing import Any


class MCPError(Exception):
    """Root of the MCP error tree."""

    def __init__(
        self,
        message: str,
        *,
        server_id: str | None = None,
        hint: str | None = None,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.server_id = server_id
        self.hint = hint
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "server_id": self.server_id,
            "message": self.message,
            "hint": self.hint,
        }

    def display(self) -> str:
        """One-line rendering for the CLI / tool result."""
        who = f"[{self.server_id}] " if self.server_id else ""
        body = f"{who}{self.message}"
        return body if not self.hint else f"{body}\n提示: {self.hint}"


class MCPConfigError(MCPError):
    """Server config is missing/invalid (analogous to ConfigError)."""


class MCPTransportError(MCPError):
    """The byte channel failed: spawn failed, process died, EOF, broken pipe."""


class MCPConnectError(MCPTransportError):
    """Could not establish the connection at all."""


class MCPTimeoutError(MCPTransportError):
    """A request did not get a response within the deadline."""


class MCPProtocolError(MCPError):
    """A JSON-RPC / MCP protocol violation (bad frame, error response, etc.)."""


class MCPVersionMismatch(MCPProtocolError):
    """Server speaks a protocol version we don't support."""


class MCPCapabilityError(MCPError):
    """Called a capability (tools/resources/prompts) the server didn't advertise."""


class MCPToolError(MCPError):
    """``tools/call`` returned ``isError`` or an unusable result."""


class MCPSecurityError(MCPError):
    """A call was blocked by the local ``ToolPolicy`` guard (analogous to PathDenied)."""


__all__ = [
    "MCPError",
    "MCPConfigError",
    "MCPTransportError",
    "MCPConnectError",
    "MCPTimeoutError",
    "MCPProtocolError",
    "MCPVersionMismatch",
    "MCPCapabilityError",
    "MCPToolError",
    "MCPSecurityError",
]
