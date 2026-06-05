"""MCPClientSession — the synchronous RPC front door for one server.

Wraps a :class:`Transport` and turns the project's *synchronous* world into
MCP calls. The async-vs-sync impedance flagged in the architecture risk table
is resolved here: this class blocks. The transport's reader thread does the
only concurrency, and ``_call`` waits for the matching response id.

Lifecycle: ``connect()`` → ``list_*`` / ``call_tool`` / ``read_resource`` /
``get_prompt`` → ``close()``. A failed connect marks the session ``FAILED``
and is isolated by the manager — it never takes down siblings.
"""

from __future__ import annotations

import threading
from typing import Any

from ..config import MCPServerConfig
from ..errors import (
    MCPCapabilityError,
    MCPError,
    MCPProtocolError,
    MCPToolError,
)
from ..logging import log_lifecycle, save_mcp_frame
from ..protocol.capabilities import HandshakeResult
from ..protocol.codec import METHOD_NOT_FOUND, encode_line  # noqa: F401 (encode via transport)
from ..protocol.messages import JsonRpcRequest
from ..protocol.types import (
    CallToolResult,
    GetPromptResult,
    MCPPrompt,
    MCPResource,
    MCPTool,
    ReadResourceResult,
)
from ..transport.base import Transport
from ..transport.factory import build_transport
from .handshake import perform_handshake
from .lifecycle import SessionState, can_transition


class MCPClientSession:
    def __init__(self, cfg: MCPServerConfig, *, transport: Transport | None = None,
                 verbose: bool = False) -> None:
        self.cfg = cfg
        self.server_id = cfg.server_id
        self.transport = transport or build_transport(cfg)
        self.verbose = verbose
        self.state = SessionState.NEW
        self.handshake: HandshakeResult | None = None
        self._next_id = 0
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------------
    def _set_state(self, dst: SessionState) -> None:
        if self.state == dst:
            return
        if not can_transition(self.state, dst):
            # Defensive: never raise on a benign re-close.
            if dst == SessionState.CLOSED:
                self.state = dst
            return
        self.state = dst

    # ------------------------------------------------------------------
    # Low-level RPC
    # ------------------------------------------------------------------
    def _notify(self, method: str, params: dict[str, Any] | None) -> None:
        from ..protocol.messages import JsonRpcNotification

        frame = JsonRpcNotification(method=method, params=params).to_wire()
        if self.verbose:
            save_mcp_frame(server_id=self.server_id, direction="out", method=method, frame=frame)
        with self._send_lock:
            self.transport.send(frame)

    def _call(self, method: str, params: dict[str, Any] | None,
              *, timeout: float | None = None) -> Any:
        """Send a request and block for the response with the matching id.

        Skips/handles interleaved server traffic: notifications are logged and
        ignored; server→client requests get a ``method not found`` reply
        (sampling etc. arrive in M7) and we keep waiting for *our* id.
        """
        self._next_id += 1
        req_id = self._next_id
        req = JsonRpcRequest(id=req_id, method=method, params=params)
        frame = req.to_wire()
        if self.verbose:
            save_mcp_frame(server_id=self.server_id, direction="out", method=method, frame=frame)
        with self._send_lock:
            self.transport.send(frame)

        deadline = timeout if timeout is not None else self.cfg.request_timeout
        while True:
            msg = self.transport.receive(timeout=deadline)
            if self.verbose:
                save_mcp_frame(server_id=self.server_id, direction="in",
                               method=msg.kind, frame=msg.raw)

            if msg.kind == "notification" and msg.notification is not None:
                log_lifecycle(self.server_id, "notification", msg.notification.method)
                continue
            if msg.kind == "request" and msg.request is not None:
                self._reject_server_request(msg.request)
                continue

            resp = msg.response
            if resp is None:
                continue
            if resp.id != req_id:
                # Out-of-order / stale response — ignore and keep waiting.
                continue
            if resp.is_error and resp.error is not None:
                raise MCPProtocolError(
                    f"{method} 失败: [{resp.error.code}] {resp.error.message}",
                    server_id=self.server_id,
                    raw=resp.error.data,
                )
            return resp.result

    def _reject_server_request(self, req: JsonRpcRequest) -> None:
        """Reply 'method not found' to any server-initiated request (M7 territory)."""
        from ..protocol.messages import JSONRPC_VERSION

        frame = {
            "jsonrpc": JSONRPC_VERSION,
            "id": req.id,
            "error": {"code": METHOD_NOT_FOUND,
                      "message": f"client 暂不支持 {req.method}（计划在 M7）"},
        }
        try:
            with self._send_lock:
                self.transport.send(frame)
        except MCPError:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> HandshakeResult:
        self._set_state(SessionState.CONNECTING)
        try:
            self.transport.start(timeout=self.cfg.connect_timeout)
            self.handshake = perform_handshake(
                server_id=self.server_id,
                call=lambda m, p: self._call(m, p, timeout=self.cfg.connect_timeout),
                notify=self._notify,
            )
        except MCPError:
            self._set_state(SessionState.FAILED)
            raise
        self._set_state(SessionState.READY)
        log_lifecycle(self.server_id, "ready",
                      f"server={self.handshake.server_name} v={self.handshake.server_version}")
        return self.handshake

    def close(self) -> None:
        try:
            self.transport.close()
        finally:
            self._set_state(SessionState.CLOSED)

    # ------------------------------------------------------------------
    def _require(self, feature: str) -> None:
        if self.state != SessionState.READY:
            raise MCPError(f"session 未就绪（state={self.state.value}）", server_id=self.server_id)
        caps = self.handshake.capabilities if self.handshake else None
        if caps is None or not caps.supports(feature):
            raise MCPCapabilityError(
                f"server 未声明 {feature} 能力",
                server_id=self.server_id,
                hint=f"该 server 不提供 {feature}；检查 server 版本或换一个 server",
            )

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    def list_tools(self) -> list[MCPTool]:
        self._require("tools")
        result = self._call("tools/list", {})
        items = (result or {}).get("tools") or []
        return [MCPTool.from_wire(t) for t in items if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self._require("tools")
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise MCPToolError(
                f"tools/call 返回异常类型 {type(result).__name__}",
                server_id=self.server_id, raw=result,
            )
        return CallToolResult.from_wire(result)

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    def list_resources(self) -> list[MCPResource]:
        self._require("resources")
        result = self._call("resources/list", {})
        items = (result or {}).get("resources") or []
        return [MCPResource.from_wire(r) for r in items if isinstance(r, dict)]

    def read_resource(self, uri: str) -> ReadResourceResult:
        self._require("resources")
        result = self._call("resources/read", {"uri": uri})
        if not isinstance(result, dict):
            raise MCPProtocolError("resources/read 返回异常", server_id=self.server_id, raw=result)
        return ReadResourceResult.from_wire(result)

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------
    def list_prompts(self) -> list[MCPPrompt]:
        self._require("prompts")
        result = self._call("prompts/list", {})
        items = (result or {}).get("prompts") or []
        return [MCPPrompt.from_wire(p) for p in items if isinstance(p, dict)]

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:
        self._require("prompts")
        result = self._call("prompts/get", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise MCPProtocolError("prompts/get 返回异常", server_id=self.server_id, raw=result)
        return GetPromptResult.from_wire(result)


__all__ = ["MCPClientSession"]
