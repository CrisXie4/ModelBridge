"""MCP session layer: one server's lifecycle + synchronous RPC front door."""

from __future__ import annotations

from .client_session import MCPClientSession
from .lifecycle import SessionState, can_transition

__all__ = ["MCPClientSession", "SessionState", "can_transition"]
