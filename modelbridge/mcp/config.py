"""MCP server configuration.

Servers are declared under a top-level ``mcp:`` key in
``~/.modelbridge/config.yaml``::

    mcp:
      enabled: true
      servers:
        - id: filesystem
          transport: stdio
          command: npx
          args: ["-y", "@modelcontextprotocol/server-filesystem", "/some/dir"]
          env: {}
          enabled: true
          connect_timeout: 20
          request_timeout: 60
          tool_policy: approve   # auto | approve | deny

We read the raw ``mcp`` block off the already-validated :class:`AppConfig`'s
``extra`` (AppConfig uses ``extra="allow"`` is NOT set, so we re-read the raw
YAML instead — see :func:`load_mcp_settings`). Keeping MCP config in its own
loader avoids touching the pydantic ``AppConfig`` schema for this milestone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..config import _safe_load_yaml  # reuse the project's YAML reader
from ..utils import get_config_path
from .errors import MCPConfigError


class TransportKind(str, Enum):
    STDIO = "stdio"
    HTTP = "http"  # implemented in M4; declared here so config validates early


class ToolPolicyKind(str, Enum):
    AUTO = "auto"        # call without prompting
    APPROVE = "approve"  # ask via AgentContext.confirm before each call
    DENY = "deny"        # never call (discovery only)


@dataclass
class MCPServerConfig:
    server_id: str
    transport: TransportKind = TransportKind.STDIO
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    enabled: bool = True
    connect_timeout: float = 20.0
    request_timeout: float = 60.0
    tool_policy: ToolPolicyKind = ToolPolicyKind.APPROVE

    # ------------------------------------------------------------------
    def validate(self) -> None:
        if not self.server_id:
            raise MCPConfigError("MCP server 缺少 id", hint="给每个 server 配一个唯一的 id")
        if self.transport == TransportKind.STDIO and not self.command:
            raise MCPConfigError(
                f"stdio server '{self.server_id}' 缺少 command",
                server_id=self.server_id,
                hint="例如 command: npx, args: ['-y', '@modelcontextprotocol/server-...']",
            )
        if self.transport == TransportKind.HTTP and not self.url:
            raise MCPConfigError(
                f"http server '{self.server_id}' 缺少 url",
                server_id=self.server_id,
            )

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "MCPServerConfig":
        if not isinstance(raw, dict):
            raise MCPConfigError(f"server 条目必须是映射，收到 {type(raw).__name__}")
        try:
            transport = TransportKind(str(raw.get("transport", "stdio")).lower())
        except ValueError:
            raise MCPConfigError(
                f"未知 transport: {raw.get('transport')!r}",
                hint="目前支持 stdio（http 在 M4）",
            ) from None
        try:
            policy = ToolPolicyKind(str(raw.get("tool_policy", "approve")).lower())
        except ValueError:
            policy = ToolPolicyKind.APPROVE
        args = raw.get("args") or []
        if not isinstance(args, list):
            raise MCPConfigError("args 必须是列表", server_id=str(raw.get("id")))
        env = raw.get("env") or {}
        if not isinstance(env, dict):
            raise MCPConfigError("env 必须是映射", server_id=str(raw.get("id")))
        cfg = cls(
            server_id=str(raw.get("id") or raw.get("server_id") or ""),
            transport=transport,
            command=raw.get("command"),
            args=[str(a) for a in args],
            env={str(k): str(v) for k, v in env.items()},
            url=raw.get("url"),
            enabled=bool(raw.get("enabled", True)),
            connect_timeout=float(raw.get("connect_timeout", 20.0)),
            request_timeout=float(raw.get("request_timeout", 60.0)),
            tool_policy=policy,
        )
        cfg.validate()
        return cfg


@dataclass
class MCPSettings:
    enabled: bool = False
    servers: list[MCPServerConfig] = field(default_factory=list)

    def enabled_servers(self) -> list[MCPServerConfig]:
        return [s for s in self.servers if s.enabled]


def load_mcp_settings() -> MCPSettings:
    """Read the ``mcp:`` block from ``~/.modelbridge/config.yaml``.

    Returns a disabled, empty :class:`MCPSettings` when the block is absent —
    so callers can always call this without a guard.
    """
    raw = _safe_load_yaml(get_config_path())
    block = raw.get("mcp") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return MCPSettings()

    enabled = bool(block.get("enabled", True))
    servers_raw = block.get("servers") or []
    if not isinstance(servers_raw, list):
        raise MCPConfigError("mcp.servers 必须是列表")

    servers: list[MCPServerConfig] = []
    seen: set[str] = set()
    for entry in servers_raw:
        cfg = MCPServerConfig.from_raw(entry)
        if cfg.server_id in seen:
            raise MCPConfigError(
                f"重复的 server id: {cfg.server_id}",
                hint="每个 MCP server 的 id 必须唯一",
            )
        seen.add(cfg.server_id)
        servers.append(cfg)
    return MCPSettings(enabled=enabled, servers=servers)


__all__ = [
    "TransportKind",
    "ToolPolicyKind",
    "MCPServerConfig",
    "MCPSettings",
    "load_mcp_settings",
]
