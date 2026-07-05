"""Protocol version + capability negotiation for the MCP handshake.

ModelBridge is a *client*. On ``initialize`` we send our protocol version and
client capabilities; the server replies with its info + advertised
capabilities. We only call into a capability the server actually advertised —
otherwise :class:`modelbridge.mcp.errors.MCPCapabilityError` is raised before
hitting the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ... import __version__

# The MCP protocol revision we implement against. Servers may speak an older
# or newer revision; the handshake records what the server returned and we
# proceed with the intersection of capabilities.
PROTOCOL_VERSION = "2025-06-18"

# Older revisions we still consider compatible enough to proceed against.
KNOWN_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def client_info() -> dict[str, Any]:
    return {"name": "modelbridge", "version": str(__version__)}


def client_capabilities(*, sampling: bool = False) -> dict[str, Any]:
    """What *we* support as a client.

    We consume tools/resources/prompts. With ``sampling=True`` (M7, gated by
    ``mcp.sampling.enabled``) we also advertise ``sampling`` so servers may
    borrow our configured models via ``sampling/createMessage``.
    """
    caps: dict[str, Any] = {}
    if sampling:
        caps["sampling"] = {}
    return caps


@dataclass
class ServerCapabilities:
    """Parsed view of the server's advertised capabilities."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False
    logging: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "ServerCapabilities":
        caps = raw if isinstance(raw, dict) else {}
        return cls(
            tools="tools" in caps,
            resources="resources" in caps,
            prompts="prompts" in caps,
            logging="logging" in caps,
            raw=caps,
        )

    def supports(self, feature: str) -> bool:
        return bool(getattr(self, feature, False))


@dataclass
class HandshakeResult:
    protocol_version: str
    server_name: str
    server_version: str
    capabilities: ServerCapabilities
    instructions: str = ""


__all__ = [
    "PROTOCOL_VERSION",
    "KNOWN_VERSIONS",
    "client_info",
    "client_capabilities",
    "ServerCapabilities",
    "HandshakeResult",
]
