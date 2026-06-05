"""Transport factory — pick an implementation from config.

Mirrors the provider ``_REGISTRY`` pattern: a transport-kind → builder map,
so M4's HTTP transport plugs in by adding one entry.
"""

from __future__ import annotations

from typing import Callable

from ..config import MCPServerConfig, TransportKind
from ..errors import MCPConfigError
from .base import Transport
from .stdio import StdioTransport


def _build_stdio(cfg: MCPServerConfig) -> Transport:
    assert cfg.command is not None  # guaranteed by MCPServerConfig.validate
    return StdioTransport(
        server_id=cfg.server_id,
        command=cfg.command,
        args=cfg.args,
        env=cfg.env,
    )


_BUILDERS: dict[TransportKind, Callable[[MCPServerConfig], Transport]] = {
    TransportKind.STDIO: _build_stdio,
}


def build_transport(cfg: MCPServerConfig) -> Transport:
    builder = _BUILDERS.get(cfg.transport)
    if builder is None:
        raise MCPConfigError(
            f"transport {cfg.transport.value!r} 尚未实现",
            server_id=cfg.server_id,
            hint="目前仅支持 stdio；http 计划在 M4 落地",
        )
    return builder(cfg)


__all__ = ["build_transport"]
