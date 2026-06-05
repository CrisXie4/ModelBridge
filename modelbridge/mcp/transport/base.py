"""Transport ABC: bytes <-> JSON-RPC frame, no protocol semantics.

A transport owns the channel to one server. It knows how to start the
channel, push an outbound frame, pull the next inbound frame (blocking with a
deadline), and close. It does **not** assign ids, match responses, or
understand MCP — that's the session layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..protocol.messages import IncomingMessage


class Transport(ABC):
    """Abstract bidirectional JSON-RPC frame channel."""

    server_id: str = ""

    @abstractmethod
    def start(self, *, timeout: float) -> None:
        """Open the channel (spawn process / connect). Raise MCPConnectError on failure."""

    @abstractmethod
    def send(self, frame: dict[str, Any]) -> None:
        """Write one frame. Raise MCPTransportError if the channel is dead."""

    @abstractmethod
    def receive(self, *, timeout: float) -> IncomingMessage:
        """Block until the next inbound frame, or raise MCPTimeoutError on deadline.

        Raises MCPTransportError if the channel closed (EOF).
        """

    @abstractmethod
    def close(self) -> None:
        """Tear down the channel. Must be idempotent and never raise."""

    @abstractmethod
    def is_alive(self) -> bool:
        ...


__all__ = ["Transport"]
