"""The MCP ``initialize`` / ``notifications/initialized`` handshake.

Pure flow logic given a low-level "call" function — kept separate from
:class:`MCPClientSession` so it can be unit-tested with a fake transport.
"""

from __future__ import annotations

from typing import Any, Callable

from ..errors import MCPProtocolError, MCPVersionMismatch
from ..protocol.capabilities import (
    KNOWN_VERSIONS,
    PROTOCOL_VERSION,
    HandshakeResult,
    ServerCapabilities,
    client_capabilities,
    client_info,
)

# call(method, params) -> result dict ; notify(method, params) -> None
CallFn = Callable[[str, dict[str, Any] | None], Any]
NotifyFn = Callable[[str, dict[str, Any] | None], None]


def perform_handshake(*, server_id: str, call: CallFn, notify: NotifyFn) -> HandshakeResult:
    result = call(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": client_capabilities(),
            "clientInfo": client_info(),
        },
    )
    if not isinstance(result, dict):
        raise MCPProtocolError(
            "initialize 返回的不是对象", server_id=server_id, raw=result
        )

    server_version = str(result.get("protocolVersion") or "")
    if server_version and server_version not in KNOWN_VERSIONS:
        # Not fatal by itself, but flag clearly — many servers are forward-compatible.
        raise MCPVersionMismatch(
            f"server 协议版本 {server_version!r} 不在已知集合内 {sorted(KNOWN_VERSIONS)}",
            server_id=server_id,
            hint="升级 ModelBridge 或该 MCP server 到匹配的协议版本",
        )

    info = result.get("serverInfo") or {}
    caps = ServerCapabilities.from_wire(result.get("capabilities") or {})

    # Tell the server we're ready (fire-and-forget notification).
    notify("notifications/initialized", None)

    return HandshakeResult(
        protocol_version=server_version or PROTOCOL_VERSION,
        server_name=str(info.get("name") or server_id),
        server_version=str(info.get("version") or ""),
        capabilities=caps,
        instructions=str(result.get("instructions") or ""),
    )


__all__ = ["perform_handshake"]
