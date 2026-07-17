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
    HTTP = "http"  # Streamable HTTP (POST + SSE)


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
    # stdio server 的工作目录。``python -m app`` 这类入口必须在这里指向
    # 包根目录，否则子进程在错误的 cwd 启动会找不到目标模块。
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)  # http only (e.g. Authorization)
    enabled: bool = True
    connect_timeout: float = 20.0
    request_timeout: float = 60.0
    tool_policy: ToolPolicyKind = ToolPolicyKind.APPROVE
    # Per-tool policy overrides (raw tool name → policy), finer than tool_policy:
    #   tool_overrides: {delete_file: deny, search: auto}
    tool_overrides: dict[str, ToolPolicyKind] = field(default_factory=dict)

    def policy_for_tool(self, raw_tool_name: str) -> ToolPolicyKind:
        return self.tool_overrides.get(raw_tool_name, self.tool_policy)

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
                hint="例如 url: https://example.com/mcp（可配 headers 携带鉴权）",
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
                hint="可选值: stdio | http",
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
        headers = raw.get("headers") or {}
        if not isinstance(headers, dict):
            raise MCPConfigError("headers 必须是映射", server_id=str(raw.get("id")))
        overrides_raw = raw.get("tool_overrides") or {}
        if not isinstance(overrides_raw, dict):
            raise MCPConfigError("tool_overrides 必须是映射", server_id=str(raw.get("id")))
        overrides: dict[str, ToolPolicyKind] = {}
        for tool_name, p in overrides_raw.items():
            try:
                overrides[str(tool_name)] = ToolPolicyKind(str(p).lower())
            except ValueError:
                raise MCPConfigError(
                    f"tool_overrides.{tool_name} 的策略非法: {p!r}",
                    server_id=str(raw.get("id")),
                    hint="可选值: auto | approve | deny",
                ) from None
        cfg = cls(
            server_id=str(raw.get("id") or raw.get("server_id") or ""),
            transport=transport,
            command=raw.get("command"),
            args=[str(a) for a in args],
            env={str(k): str(v) for k, v in env.items()},
            cwd=str(raw["cwd"]) if raw.get("cwd") else None,
            url=raw.get("url"),
            headers={str(k): str(v) for k, v in headers.items()},
            enabled=bool(raw.get("enabled", True)),
            connect_timeout=float(raw.get("connect_timeout", 20.0)),
            request_timeout=float(raw.get("request_timeout", 60.0)),
            tool_policy=policy,
            tool_overrides=overrides,
        )
        cfg.validate()
        return cfg


@dataclass
class MCPSettings:
    enabled: bool = False
    servers: list[MCPServerConfig] = field(default_factory=list)
    # M5 — robustness knobs.
    reconnect_attempts: int = 2      # per failure; 0 disables auto-reconnect
    reconnect_backoff: float = 0.5   # base delay, doubled per attempt
    heartbeat_interval: float = 0.0  # seconds between pings; 0 disables
    # M7 — let servers borrow our models via sampling/createMessage.
    sampling_enabled: bool = False
    sampling_model: str | None = None      # None → config default_model
    sampling_max_tokens: int = 2048        # per-call output ceiling
    sampling_max_calls: int = 32           # per-server, per-session call ceiling

    def enabled_servers(self) -> list[MCPServerConfig]:
        return [s for s in self.servers if s.enabled]


def _coerce_number(raw: dict, key: str, default, cast, label: str | None = None):
    """Cast a config value to int/float, raising MCPConfigError on bad type.

    ``label`` overrides the field name used in the error message (useful for
    nested keys like ``mcp.sampling.max_tokens``).
    """
    if key not in raw:
        return default
    try:
        return cast(raw[key])
    except (TypeError, ValueError):
        field_name = label if label is not None else f"mcp.{key}"
        raise MCPConfigError(
            f"{field_name} 必须是数字，得到 {raw[key]!r}",
            hint="检查 config.yaml 的 mcp 配置块",
        ) from None


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

    sampling_raw = block.get("sampling")
    sampling = sampling_raw if isinstance(sampling_raw, dict) else {}

    return MCPSettings(
        enabled=enabled,
        servers=servers,
        reconnect_attempts=_coerce_number(block, "reconnect_attempts", 2, int),
        reconnect_backoff=_coerce_number(block, "reconnect_backoff", 0.5, float),
        heartbeat_interval=_coerce_number(block, "heartbeat_interval", 0.0, float),
        sampling_enabled=bool(sampling.get("enabled", False)),
        sampling_model=sampling.get("model"),
        sampling_max_tokens=_coerce_number(
            sampling, "max_tokens", 2048, int, label="mcp.sampling.max_tokens"
        ),
        sampling_max_calls=_coerce_number(
            sampling, "max_calls", 32, int, label="mcp.sampling.max_calls"
        ),
    )


__all__ = [
    "TransportKind",
    "ToolPolicyKind",
    "MCPServerConfig",
    "MCPSettings",
    "load_mcp_settings",
]
