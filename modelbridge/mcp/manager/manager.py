"""MCPManager — connect, govern, and dispatch across N servers.

Owns the sessions, builds the :class:`Catalog`, and exposes qualified-name
dispatch for tool calls / resource reads / prompt fetches. Failure isolation
is the headline guarantee: one server failing to connect marks just that
session ``FAILED`` and is recorded in ``connect_errors``; every other server
stays usable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import MCPServerConfig, MCPSettings, ToolPolicyKind, load_mcp_settings
from ..errors import MCPError
from ..logging import log_lifecycle
from ..protocol.types import CallToolResult, GetPromptResult, ReadResourceResult
from ..session.client_session import MCPClientSession
from ..session.lifecycle import SessionState
from .catalog import Catalog


@dataclass
class ServerStatus:
    server_id: str
    state: str
    server_name: str = ""
    server_version: str = ""
    tools: int = 0
    resources: int = 0
    prompts: int = 0
    error: str | None = None


@dataclass
class MCPManager:
    settings: MCPSettings
    verbose: bool = False
    sessions: dict[str, MCPClientSession] = field(default_factory=dict)
    catalog: Catalog = field(default_factory=Catalog)
    connect_errors: dict[str, MCPError] = field(default_factory=dict)
    # server_id -> tool policy, for the adapter layer to consult.
    policies: dict[str, ToolPolicyKind] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, *, verbose: bool = False) -> "MCPManager":
        return cls(settings=load_mcp_settings(), verbose=verbose)

    # ------------------------------------------------------------------
    def connect_all(self) -> Catalog:
        """Connect every enabled server, isolating per-server failures.

        Connections run sequentially here (M0–M3). M5 can parallelise; the
        catalog build and failure isolation are already independent per server.
        """
        for cfg in self.settings.enabled_servers():
            self._connect_one(cfg)
        log_lifecycle("*", "connect_all", str(self.catalog.counts()))
        return self.catalog

    def _connect_one(self, cfg: MCPServerConfig) -> None:
        self.policies[cfg.server_id] = cfg.tool_policy
        session = MCPClientSession(cfg, verbose=self.verbose)
        self.sessions[cfg.server_id] = session
        try:
            hs = session.connect()
            tools = session.list_tools() if hs.capabilities.tools else []
            resources = session.list_resources() if hs.capabilities.resources else []
            prompts = session.list_prompts() if hs.capabilities.prompts else []
            self.catalog.add_server(
                cfg.server_id, tools=tools, resources=resources, prompts=prompts
            )
        except MCPError as e:
            self.connect_errors[cfg.server_id] = e
            log_lifecycle(cfg.server_id, "connect_failed", e.message)
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def session_for(self, server_id: str) -> MCPClientSession:
        s = self.sessions.get(server_id)
        if s is None or s.state != SessionState.READY:
            raise MCPError(
                f"server '{server_id}' 不可用",
                server_id=server_id,
                hint="该 server 可能连接失败；运行 `mbridge mcp list` 查看状态",
            )
        return s

    # ------------------------------------------------------------------
    # Dispatch by qualified name
    # ------------------------------------------------------------------
    def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> CallToolResult:
        resolved = self.catalog.resolve_tool(qualified_name)
        if resolved is None:
            raise MCPError(
                f"未知 MCP tool: {qualified_name}",
                hint="名称形如 <server_id>__<tool>；用 `mbridge mcp tools` 查看",
            )
        server_id, raw_name = resolved
        return self.session_for(server_id).call_tool(raw_name, arguments)

    def read_resource(self, uri: str, *, server_id: str | None = None) -> ReadResourceResult:
        if server_id is None:
            q = self.catalog.find_resource(uri)
            if q is None:
                raise MCPError(f"未知 resource uri: {uri}")
            server_id = q.server_id
        return self.session_for(server_id).read_resource(uri)

    def get_prompt(self, qualified_name: str,
                   arguments: dict[str, Any] | None = None) -> GetPromptResult:
        resolved = self.catalog.resolve_prompt(qualified_name)
        if resolved is None:
            raise MCPError(
                f"未知 MCP prompt: {qualified_name}",
                hint="名称形如 <server_id>__<prompt>；用 `mbridge mcp prompts` 查看",
            )
        server_id, raw_name = resolved
        return self.session_for(server_id).get_prompt(raw_name, arguments)

    def policy_for(self, server_id: str) -> ToolPolicyKind:
        return self.policies.get(server_id, ToolPolicyKind.APPROVE)

    # ------------------------------------------------------------------
    def statuses(self) -> list[ServerStatus]:
        out: list[ServerStatus] = []
        counts_by_server: dict[str, dict[str, int]] = {}
        for qt in self.catalog.tools:
            counts_by_server.setdefault(qt.server_id, {}).setdefault("tools", 0)
            counts_by_server[qt.server_id]["tools"] += 1
        for qr in self.catalog.resources:
            counts_by_server.setdefault(qr.server_id, {}).setdefault("resources", 0)
            counts_by_server[qr.server_id]["resources"] = counts_by_server[qr.server_id].get("resources", 0) + 1
        for qp in self.catalog.prompts:
            counts_by_server.setdefault(qp.server_id, {}).setdefault("prompts", 0)
            counts_by_server[qp.server_id]["prompts"] = counts_by_server[qp.server_id].get("prompts", 0) + 1

        for cfg in self.settings.servers:
            sid = cfg.server_id
            session = self.sessions.get(sid)
            err = self.connect_errors.get(sid)
            if not cfg.enabled:
                out.append(ServerStatus(server_id=sid, state="disabled"))
                continue
            hs = session.handshake if session else None
            c = counts_by_server.get(sid, {})
            # A connect error wins over the post-cleanup CLOSED state: the user
            # cares that it *failed*, and the detail is in connect_errors.
            state = "failed" if err is not None else (session.state.value if session else "new")
            out.append(ServerStatus(
                server_id=sid,
                state=state,
                server_name=hs.server_name if hs else "",
                server_version=hs.server_version if hs else "",
                tools=c.get("tools", 0),
                resources=c.get("resources", 0),
                prompts=c.get("prompts", 0),
                error=err.display() if err else None,
            ))
        return out

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        for s in self.sessions.values():
            try:
                s.close()
            except Exception:  # noqa: BLE001 — shutdown must not raise
                pass

    def __enter__(self) -> "MCPManager":
        self.connect_all()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()


__all__ = ["MCPManager", "ServerStatus"]
