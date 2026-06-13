"""Tests for MCP M4–M7: HTTP transport, robustness, deep integration, server mode.

The HTTP tests run a tiny in-process Streamable-HTTP server (JSON + SSE
responses, session-id header). The robustness/sampling tests reuse
``mcp_fake_server.py`` over real stdio with behaviour flags.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from modelbridge.agent.tools.registry import ToolRegistry
from modelbridge.mcp import (
    MCPManager,
    MCPServerConfig,
    MCPSettings,
    ToolPolicyKind,
    TransportKind,
    register_mcp_tools,
    sync_mcp_tools,
)
from modelbridge.mcp.config import load_mcp_settings
from modelbridge.mcp.errors import MCPError
from modelbridge.mcp.server import MCPServer, ServerTool, build_modelbridge_server
from modelbridge.mcp.session.client_session import MCPClientSession
from modelbridge.mcp.session.lifecycle import ReconnectPolicy

FAKE = str(Path(__file__).parent / "mcp_fake_server.py")


def _stdio_cfg(server_id="fake", extra_args=None,
               policy=ToolPolicyKind.AUTO, **kw) -> MCPServerConfig:
    return MCPServerConfig(
        server_id=server_id,
        transport=TransportKind.STDIO,
        command=sys.executable,
        args=[FAKE, *(extra_args or [])],
        connect_timeout=15.0,
        request_timeout=15.0,
        tool_policy=policy,
        **kw,
    )


# ---------------------------------------------------------------------------
# M4 — Streamable HTTP transport
# ---------------------------------------------------------------------------

class _HttpMcpHandler(BaseHTTPRequestHandler):
    """Minimal Streamable-HTTP MCP server: JSON replies, SSE for tools/call."""

    SESSION_ID = "sess-123"
    seen_session_headers: list[str | None] = []

    def log_message(self, *a):  # noqa: D102, ANN002 — silence test output
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        msg = json.loads(body)
        self.seen_session_headers.append(self.headers.get("mcp-session-id"))
        method = msg.get("method")
        msg_id = msg.get("id")

        if msg_id is None:  # notification
            self.send_response(202)
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-fake", "version": "0.2"},
            }
            self._json_reply(msg_id, result, session=True)
        elif method == "tools/list":
            result = {"tools": [{
                "name": "add",
                "description": "Add two ints.",
                "inputSchema": {"type": "object",
                                "properties": {"a": {"type": "integer"},
                                               "b": {"type": "integer"}},
                                "required": ["a", "b"]},
            }]}
            self._json_reply(msg_id, result)
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            total = int(args.get("a", 0)) + int(args.get("b", 0))
            # Exercise the SSE path: a notification, then the response.
            frames = [
                {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
                {"jsonrpc": "2.0", "id": msg_id,
                 "result": {"content": [{"type": "text", "text": str(total)}],
                            "isError": False}},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for f in frames:
                payload = f"data: {json.dumps(f)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        else:
            self._json_reply(msg_id, {}, error={"code": -32601,
                                                "message": "method not found"})

    def do_GET(self):  # noqa: N802 — no standalone listen stream offered
        self.send_response(405)
        self.end_headers()

    def do_DELETE(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def _json_reply(self, msg_id, result, *, session=False, error=None):
        frame = {"jsonrpc": "2.0", "id": msg_id}
        if error is not None:
            frame["error"] = error
        else:
            frame["result"] = result
        data = json.dumps(frame).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if session:
            self.send_header("Mcp-Session-Id", self.SESSION_ID)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def http_mcp_url():
    _HttpMcpHandler.seen_session_headers = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpMcpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()
    server.server_close()


def _http_cfg(url: str) -> MCPServerConfig:
    return MCPServerConfig(
        server_id="remote",
        transport=TransportKind.HTTP,
        url=url,
        connect_timeout=10.0,
        request_timeout=10.0,
        tool_policy=ToolPolicyKind.AUTO,
    )


def test_http_handshake_and_tool_call(http_mcp_url):
    session = MCPClientSession(_http_cfg(http_mcp_url))
    try:
        hs = session.connect()
        assert hs.server_name == "http-fake"
        tools = session.list_tools()
        assert [t.name for t in tools] == ["add"]
        result = session.call_tool("add", {"a": 2, "b": 40})
        assert not result.is_error
        assert result.joined_text() == "42"
        # The SSE notification before the response marked the catalog dirty.
        assert "tools" in session.dirty
    finally:
        session.close()


def test_http_session_id_echoed(http_mcp_url):
    session = MCPClientSession(_http_cfg(http_mcp_url))
    try:
        session.connect()
        session.list_tools()
    finally:
        session.close()
    headers = _HttpMcpHandler.seen_session_headers
    # initialize carries no session id; everything after echoes the server's.
    assert headers[0] is None
    assert _HttpMcpHandler.SESSION_ID in headers[1:]


def test_http_config_requires_url():
    with pytest.raises(MCPError):
        MCPServerConfig(server_id="x", transport=TransportKind.HTTP).validate()


# ---------------------------------------------------------------------------
# M5 — reconnect / ping / list_changed hot refresh
# ---------------------------------------------------------------------------

def test_reconnect_policy_delays():
    p = ReconnectPolicy(max_attempts=3, base_delay=0.5, max_delay=8.0)
    assert p.delays() == [0.5, 1.0, 2.0]


def test_session_ping():
    session = MCPClientSession(_stdio_cfg())
    try:
        session.connect()
        session.ping()  # must not raise
    finally:
        session.close()


def test_manager_auto_reconnect_after_crash(tmp_path):
    marker = tmp_path / "crashed.marker"
    cfg = _stdio_cfg(extra_args=["--crash-once", str(marker)])
    settings = MCPSettings(enabled=True, servers=[cfg],
                           reconnect_attempts=2, reconnect_backoff=0.05)
    manager = MCPManager(settings=settings)
    try:
        manager.connect_all()
        # First call crashes the server; the manager reconnects and retries.
        result = manager.call_tool("fake__echo", {"text": "back"})
        assert not result.is_error
        assert result.joined_text() == "echo: back"
        assert marker.exists()
    finally:
        manager.shutdown()


def test_list_changed_hot_refresh_syncs_registry():
    cfg = _stdio_cfg(extra_args=["--notify-changed"])
    settings = MCPSettings(enabled=True, servers=[cfg],
                           reconnect_attempts=0)
    manager = MCPManager(settings=settings)
    try:
        manager.connect_all()
        registry = ToolRegistry()
        assert register_mcp_tools(registry, manager) == 1
        assert registry.names() == ["fake__echo"]
        # The call triggers list_changed; call_tool hot-refreshes the catalog
        # and the bound registry picks up the new tool automatically.
        manager.call_tool("fake__echo", {"text": "x"})
        assert "fake__shout" in registry.names()
        result = manager.call_tool("fake__shout", {"text": "hey"})
        assert result.joined_text() == "SHOUT: HEY"
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# M6 — runtime toggling + per-tool policy overrides
# ---------------------------------------------------------------------------

def test_runtime_disable_unregisters_and_blocks():
    settings = MCPSettings(enabled=True, servers=[_stdio_cfg()])
    manager = MCPManager(settings=settings)
    try:
        manager.connect_all()
        registry = ToolRegistry()
        register_mcp_tools(registry, manager)
        assert "fake__echo" in registry.names()

        assert manager.set_server_enabled("fake", False)
        assert registry.names() == []  # bound registry auto-synced
        with pytest.raises(MCPError):
            manager.call_tool("fake__echo", {"text": "x"})
        statuses = {s.server_id: s.state for s in manager.statuses()}
        assert statuses["fake"] == "paused"

        assert manager.set_server_enabled("fake", True)
        assert "fake__echo" in registry.names()
        assert not manager.call_tool("fake__echo", {"text": "y"}).is_error
    finally:
        manager.shutdown()


def test_tool_overrides_deny_skips_registration():
    cfg = _stdio_cfg(policy=ToolPolicyKind.AUTO,
                     tool_overrides={"echo": ToolPolicyKind.DENY})
    manager = MCPManager(settings=MCPSettings(enabled=True, servers=[cfg]))
    try:
        manager.connect_all()
        registry = ToolRegistry()
        assert register_mcp_tools(registry, manager) == 0
    finally:
        manager.shutdown()


def test_sync_mcp_tools_keeps_native_tools():
    settings = MCPSettings(enabled=True, servers=[_stdio_cfg()])
    manager = MCPManager(settings=settings)
    try:
        manager.connect_all()
        registry = ToolRegistry()

        class _Native:
            name = "native_tool"
            description = "stub"

            def json_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, args, ctx):  # noqa: ANN001
                raise NotImplementedError

            def openai_tool(self):
                return {"type": "function", "function": {"name": self.name}}

        registry.register(_Native())  # type: ignore[arg-type]
        register_mcp_tools(registry, manager)
        # Disabling auto-syncs via the bound registry and never touches
        # non-MCP tools; an explicit re-sync is then a no-op.
        manager.set_server_enabled("fake", False)
        assert registry.names() == ["native_tool"]
        added, removed = sync_mcp_tools(registry, manager)
        assert (added, removed) == (0, 0)
        assert registry.names() == ["native_tool"]
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# M5/M6 — config knobs
# ---------------------------------------------------------------------------

def test_load_settings_advanced_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "mcp:\n"
        "  enabled: true\n"
        "  reconnect_attempts: 5\n"
        "  heartbeat_interval: 30\n"
        "  sampling:\n"
        "    enabled: true\n"
        "    model: tiny-model\n"
        "    max_tokens: 512\n"
        "  servers:\n"
        "    - id: remote\n"
        "      transport: http\n"
        "      url: https://example.com/mcp\n"
        "      headers: {Authorization: 'Bearer xyz'}\n"
        "      tool_overrides: {dangerous: deny, safe: auto}\n",
        encoding="utf-8",
    )
    settings = load_mcp_settings()
    assert settings.reconnect_attempts == 5
    assert settings.heartbeat_interval == 30.0
    assert settings.sampling_enabled is True
    assert settings.sampling_model == "tiny-model"
    assert settings.sampling_max_tokens == 512
    s = settings.servers[0]
    assert s.transport == TransportKind.HTTP
    assert s.headers == {"Authorization": "Bearer xyz"}
    assert s.policy_for_tool("dangerous") == ToolPolicyKind.DENY
    assert s.policy_for_tool("safe") == ToolPolicyKind.AUTO
    assert s.policy_for_tool("other") == ToolPolicyKind.APPROVE


def test_load_settings_rejects_bad_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "mcp:\n  servers:\n"
        "    - {id: a, command: x, tool_overrides: {t: nonsense}}\n",
        encoding="utf-8",
    )
    with pytest.raises(MCPError):
        load_mcp_settings()


# ---------------------------------------------------------------------------
# M7 — sampling callback (client side)
# ---------------------------------------------------------------------------

def test_sampling_round_trip_with_fake_server():
    captured: list[dict] = []

    def fake_handler(params):
        captured.append(params)
        return {
            "role": "assistant",
            "content": {"type": "text", "text": "你好，来自国产模型"},
            "model": "fake-llm",
            "stopReason": "endTurn",
        }

    cfg = _stdio_cfg(extra_args=["--sample-on-call"])
    session = MCPClientSession(cfg, sampling_handler=fake_handler)
    try:
        session.connect()
        result = session.call_tool("echo", {"text": "ping"})
        assert result.joined_text() == "sampled: 你好，来自国产模型"
        assert captured and captured[0]["messages"][0]["content"]["text"] == "ping"
    finally:
        session.close()


def test_sampling_rejected_when_no_handler():
    cfg = _stdio_cfg(extra_args=["--sample-on-call"])
    session = MCPClientSession(cfg)  # no handler → sampling not offered
    try:
        session.connect()
        result = session.call_tool("echo", {"text": "ping"})
        assert "sampling error" in result.joined_text()
    finally:
        session.close()


def test_from_settings_wires_sampling_handler():
    """Regression: every manager built from settings (CLI subcommands included)
    must wire the sampling handler — not just MCPManager.from_config()."""
    on = MCPManager.from_settings(MCPSettings(sampling_enabled=True))
    assert on.sampling_handler is not None
    off = MCPManager.from_settings(MCPSettings(sampling_enabled=False))
    assert off.sampling_handler is None


def test_sampling_handler_builds_messages(monkeypatch):
    """build_sampling_handler maps MCP params → ChatRequest correctly."""
    from modelbridge.mcp.sampling import build_sampling_handler

    sent: dict = {}

    class _FakeProvider:
        def chat(self, request, **kw):  # noqa: ANN001
            sent["request"] = request
            from modelbridge.schemas import ChatResponse

            return ChatResponse(content="ok!", model=request.model)

    import modelbridge.mcp.sampling as sampling_mod

    monkeypatch.setattr(sampling_mod, "resolve_model_name", lambda m: "stub")
    monkeypatch.setattr(sampling_mod, "get_model_entry", lambda n: type(
        "E", (), {"model": "stub-model"})())
    monkeypatch.setattr(sampling_mod, "get_provider", lambda e: _FakeProvider())

    handler = build_sampling_handler(MCPSettings(sampling_max_tokens=100))
    result = handler({
        "systemPrompt": "be brief",
        "messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}],
        "maxTokens": 999,
    })
    req = sent["request"]
    assert [m.role for m in req.messages] == ["system", "user"]
    assert req.max_tokens == 100  # capped by settings
    assert result["content"]["text"] == "ok!"
    assert result["stopReason"] == "endTurn"


# ---------------------------------------------------------------------------
# M7 — ModelBridge as an MCP server
# ---------------------------------------------------------------------------

def test_server_handshake_and_tool_listing():
    server = build_modelbridge_server()
    init = server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
    })
    assert init["result"]["serverInfo"]["name"] == "modelbridge"
    assert "tools" in init["result"]["capabilities"]

    assert server.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.initialized

    pong = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert pong["result"] == {}

    tools = server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    names = {t["name"] for t in tools["result"]["tools"]}
    assert names == {"chat", "list_models", "route"}


def test_server_tool_call_and_errors():
    server = MCPServer(name="t", version="0")
    server.register(ServerTool(
        name="double", description="x2",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        fn=lambda args: str(int(args.get("n", 0)) * 2),
    ))
    ok = server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "double", "arguments": {"n": 21}},
    })
    assert ok["result"]["content"][0]["text"] == "42"
    assert ok["result"]["isError"] is False

    unknown_tool = server.handle_message({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "nope"},
    })
    assert unknown_tool["error"]["code"] == -32602

    unknown_method = server.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "wat"})
    assert unknown_method["error"]["code"] == -32601


def test_server_tool_exception_becomes_is_error():
    server = MCPServer(name="t", version="0")

    def boom(args):  # noqa: ANN001
        raise RuntimeError("nope")

    server.register(ServerTool(name="boom", description="",
                               input_schema={"type": "object"}, fn=boom))
    reply = server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "boom"},
    })
    assert reply["result"]["isError"] is True
    assert "RuntimeError" in reply["result"]["content"][0]["text"]


def test_server_round_trip_over_stdio(tmp_path, monkeypatch):
    """Full loop: our client ↔ our server as a real subprocess."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    cfg = MCPServerConfig(
        server_id="self",
        transport=TransportKind.STDIO,
        command=sys.executable,
        args=["-m", "modelbridge.mcp.server"],
        env={"MBRIDGE_HOME": str(tmp_path)},
        connect_timeout=20.0,
        request_timeout=20.0,
        tool_policy=ToolPolicyKind.AUTO,
    )
    session = MCPClientSession(cfg)
    try:
        hs = session.connect()
        assert hs.server_name == "modelbridge"
        tools = {t.name for t in session.list_tools()}
        assert {"chat", "list_models", "route"} <= tools
        result = session.call_tool("list_models", {})
        assert not result.is_error
        models = json.loads(result.joined_text())
        assert isinstance(models, list)
    finally:
        session.close()
