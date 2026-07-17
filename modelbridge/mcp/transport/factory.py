"""Transport factory — pick an implementation from config.

Mirrors the provider ``_REGISTRY`` pattern: a transport-kind → builder map,
so M4's HTTP transport plugs in by adding one entry.
"""

from __future__ import annotations

from typing import Callable

from ..config import MCPServerConfig, TransportKind
from ..errors import MCPConfigError
from .base import Transport
from .http import HttpTransport
from .stdio import StdioTransport


def _build_stdio(cfg: MCPServerConfig) -> Transport:
    assert cfg.command is not None  # guaranteed by MCPServerConfig.validate
    return StdioTransport(
        server_id=cfg.server_id,
        command=cfg.command,
        args=cfg.args,
        env=cfg.env,
        cwd=cfg.cwd,
    )


def _build_http(cfg: MCPServerConfig) -> Transport:
    assert cfg.url is not None  # guaranteed by MCPServerConfig.validate
    return HttpTransport(
        server_id=cfg.server_id,
        url=cfg.url,
        headers=cfg.headers,
        connect_timeout=cfg.connect_timeout,
    )


_BUILDERS: dict[TransportKind, Callable[[MCPServerConfig], Transport]] = {
    TransportKind.STDIO: _build_stdio,
    TransportKind.HTTP: _build_http,
}


def build_transport(cfg: MCPServerConfig) -> Transport:
    builder = _BUILDERS.get(cfg.transport)
    if builder is None:
        raise MCPConfigError(
            f"transport {cfg.transport.value!r} 尚未实现",
            server_id=cfg.server_id,
            hint="目前支持 stdio 与 http (Streamable HTTP)",
        )
    return builder(cfg)


__all__ = ["build_transport"]
