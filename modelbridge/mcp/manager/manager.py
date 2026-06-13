"""MCPManager — connect, govern, and dispatch across N servers.

Owns the sessions, builds the :class:`Catalog`, and exposes qualified-name
dispatch for tool calls / resource reads / prompt fetches. Failure isolation
is the headline guarantee: one server failing to connect marks just that
session ``FAILED`` and is recorded in ``connect_errors``; every other server
stays usable.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import MCPServerConfig, MCPSettings, ToolPolicyKind, load_mcp_settings
from ..errors import MCPError, MCPTransportError
from ..logging import log_lifecycle
from ..protocol.types import CallToolResult, GetPromptResult, ReadResourceResult
from ..session.client_session import MCPClientSession, SamplingHandler
from ..session.lifecycle import ReconnectPolicy, SessionState
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
    # M7 — answers servers' sampling/createMessage; built lazily from settings.
    sampling_handler: SamplingHandler | None = None
    # M6 — servers whose tools are switched off for this run (`/mcp off <id>`).
    runtime_disabled: set[str] = field(default_factory=set)
    # Called after the catalog changes (reconnect / hot refresh) so the host
    # can re-sync its ToolRegistry. Set via bind_registry().
    on_catalog_changed: Callable[[], None] | None = None

    _heartbeat_stop: threading.Event = field(default_factory=threading.Event)
    _heartbeat_thread: threading.Thread | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, *, verbose: bool = False) -> "MCPManager":
        return cls.from_settings(load_mcp_settings(), verbose=verbose)

    @classmethod
    def from_settings(cls, settings: MCPSettings, *, verbose: bool = False) -> "MCPManager":
        """Build a manager from already-loaded settings, wiring sampling (M7).

        Use this anywhere a manager is constructed from settings (CLI
        subcommands, REPL) so the sampling handler is always set *before*
        ``connect_all`` — sessions capture it at connect time.
        """
        mgr = cls(settings=settings, verbose=verbose)
        mgr._init_sampling()
        return mgr

    def _init_sampling(self) -> None:
        if self.settings.sampling_enabled and self.sampling_handler is None:
            from ..sampling import build_sampling_handler

            self.sampling_handler = build_sampling_handler(self.settings)

    @property
    def reconnect_policy(self) -> ReconnectPolicy:
        return ReconnectPolicy(
            max_attempts=self.settings.reconnect_attempts,
            base_delay=self.settings.reconnect_backoff,
        )

    # ------------------------------------------------------------------
    def connect_all(self) -> Catalog:
        """Connect every enabled server, isolating per-server failures."""
        for cfg in self.settings.enabled_servers():
            self._connect_one(cfg)
        log_lifecycle("*", "connect_all", str(self.catalog.counts()))
        return self.catalog

    def _connect_one(self, cfg: MCPServerConfig) -> None:
        self.policies[cfg.server_id] = cfg.tool_policy
        session = MCPClientSession(
            cfg, verbose=self.verbose, sampling_handler=self.sampling_handler
        )
        self.sessions[cfg.server_id] = session
        try:
            session.connect()
            self._rebuild_server_catalog(session)
        except MCPError as e:
            self.connect_errors[cfg.server_id] = e
            log_lifecycle(cfg.server_id, "connect_failed", e.message)
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    def _rebuild_server_catalog(self, session: MCPClientSession) -> None:
        """(Re)list one ready server's capabilities into the catalog."""
        hs = session.handshake
        assert hs is not None
        tools = session.list_tools() if hs.capabilities.tools else []
        resources = session.list_resources() if hs.capabilities.resources else []
        prompts = session.list_prompts() if hs.capabilities.prompts else []
        self.catalog.remove_server(session.server_id)
        self.catalog.add_server(
            session.server_id, tools=tools, resources=resources, prompts=prompts
        )
        session.dirty.clear()

    # ------------------------------------------------------------------
    # M5 — reconnect / hot refresh / heartbeat
    # ------------------------------------------------------------------
    def reconnect(self, server_id: str) -> bool:
        """Reconnect one server with exponential backoff. True on success."""
        session = self.sessions.get(server_id)
        if session is None or session.state == SessionState.CLOSED:
            return False
        for delay in self.reconnect_policy.delays():
            time.sleep(delay)
            try:
                session.reconnect()
                self._rebuild_server_catalog(session)
                self.connect_errors.pop(server_id, None)
                log_lifecycle(server_id, "reconnected")
                self._notify_catalog_changed()
                return True
            except MCPError as e:
                self.connect_errors[server_id] = e
                log_lifecycle(server_id, "reconnect_failed", e.message)
        return False

    def refresh_dirty(self) -> bool:
        """Re-list every server that announced ``list_changed``. True if changed."""
        changed = False
        for session in self.sessions.values():
            if session.state == SessionState.READY and session.dirty:
                try:
                    self._rebuild_server_catalog(session)
                    changed = True
                    log_lifecycle(session.server_id, "catalog_refreshed")
                except MCPError as e:
                    log_lifecycle(session.server_id, "refresh_failed", e.message)
        if changed:
            self._notify_catalog_changed()
        return changed

    def _notify_catalog_changed(self) -> None:
        if self.on_catalog_changed is not None:
            try:
                self.on_catalog_changed()
            except Exception:  # noqa: BLE001 — host callback must not break dispatch
                pass

    def start_heartbeat(self, interval: float | None = None) -> None:
        """Ping ready sessions every ``interval`` s; reconnect the dead ones."""
        secs = interval if interval is not None else self.settings.heartbeat_interval
        if secs <= 0 or self._heartbeat_thread is not None:
            return
        self._heartbeat_stop.clear()

        def _beat() -> None:
            while not self._heartbeat_stop.wait(secs):
                for sid, session in list(self.sessions.items()):
                    if session.state != SessionState.READY:
                        continue
                    try:
                        session.ping()
                    except MCPError:
                        log_lifecycle(sid, "heartbeat_lost")
                        self.reconnect(sid)
                self.refresh_dirty()

        self._heartbeat_thread = threading.Thread(target=_beat, daemon=True)
        self._heartbeat_thread.start()
        log_lifecycle("*", "heartbeat_started", f"interval={secs}s")

    # ------------------------------------------------------------------
    # M6 — runtime enable/disable (`/mcp on|off <server_id>`)
    # ------------------------------------------------------------------
    def set_server_enabled(self, server_id: str, enabled: bool) -> bool:
        """Toggle a server's tools for this run. False if the id is unknown."""
        if server_id not in self.sessions and not any(
            c.server_id == server_id for c in self.settings.servers
        ):
            return False
        if enabled:
            self.runtime_disabled.discard(server_id)
        else:
            self.runtime_disabled.add(server_id)
        self._notify_catalog_changed()
        return True

    def is_runtime_disabled(self, server_id: str) -> bool:
        return server_id in self.runtime_disabled

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
        if self.is_runtime_disabled(server_id):
            raise MCPError(
                f"server '{server_id}' 已在本次会话中停用",
                server_id=server_id,
                hint="在 REPL 输入 `/mcp on " + server_id + "` 重新启用",
            )
        try:
            result = self.session_for(server_id).call_tool(raw_name, arguments)
        except MCPTransportError:
            # The channel died mid-call (server crash / network blip). Try a
            # backoff reconnect, then retry exactly once (M5).
            if not self.reconnect(server_id):
                raise
            result = self.session_for(server_id).call_tool(raw_name, arguments)
        # A list_changed notification may have arrived while we waited.
        self.refresh_dirty()
        return result

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

    def policy_for_tool(self, server_id: str, raw_tool_name: str) -> ToolPolicyKind:
        """Per-tool policy: server's tool_overrides win over its tool_policy (M6)."""
        for cfg in self.settings.servers:
            if cfg.server_id == server_id:
                return cfg.policy_for_tool(raw_tool_name)
        return self.policy_for(server_id)

    # ------------------------------------------------------------------
    def bind_registry(self, registry: Any) -> None:
        """Keep ``registry`` in sync with the catalog across hot refreshes (M5/M6).

        ``registry`` is an ``agent.tools.ToolRegistry``; typed ``Any`` to keep
        the manager layer free of agent imports (the glue lives in
        :mod:`modelbridge.mcp.registry`).
        """
        from ..registry import sync_mcp_tools

        def _sync() -> None:
            sync_mcp_tools(registry, self)

        self.on_catalog_changed = _sync

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
            if sid in self.runtime_disabled and err is None:
                state = "paused"  # session alive, tools switched off via /mcp off
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
        self._heartbeat_stop.set()
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
