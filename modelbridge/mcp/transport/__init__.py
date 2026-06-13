"""MCP transport layer: byte channel <-> JSON-RPC frames."""

from __future__ import annotations

from .base import Transport
from .factory import build_transport
from .http import HttpTransport
from .stdio import StdioTransport

__all__ = ["Transport", "StdioTransport", "HttpTransport", "build_transport"]
