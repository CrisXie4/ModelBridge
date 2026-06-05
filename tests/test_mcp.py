"""Tests for the MCP module (M0–M3).

Integration tests spawn ``mcp_fake_server.py`` over real stdio, so they
exercise the transport, session, manager, and adapter end-to-end without any
network or external server.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from modelbridge.agent.context import AgentContext, auto_no, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.base import ToolCall
from modelbridge.agent.tools.registry import ToolRegistry
from modelbridge.mcp import (
    MCPManager,
    MCPServerConfig,
    MCPSettings,
    ToolPolicyKind,
    TransportKind,
    register_mcp_tools,
)
from modelbridge.mcp.config import load_mcp_settings
from modelbridge.mcp.errors import MCPError
from modelbridge.mcp.manager.naming import qualify, sanitize, split_qualified
from modelbridge.mcp.protocol.codec import decode_message, encode_line
from modelbridge.mcp.session.client_session import MCPClientSession

FAKE = str(Path(__file__).parent / "mcp_fake_server.py")


def _cfg(server_id="fake", extra_args=None, policy=ToolPolicyKind.AUTO) -> MCPServerConfig:
    return MCPServerConfig(
        server_id=server_id,
        transport=TransportKind.STDIO,
        command=sys.executable,
        args=[FAKE, *(extra_args or [])],
        connect_timeout=15.0,
        request_timeout=15.0,
        tool_policy=policy,
    )


def _ctx(approve=auto_yes) -> AgentContext:
    policy = PathPolicy(allowed_dirs=[Path.cwd()], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=Path.cwd(), approve=approve)


# ---------------------------------------------------------------------------
# Unit: codec
# ---------------------------------------------------------------------------

def test_encode_line_is_single_line():
    line = encode_line({"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert line.endswith("\n")
    assert line.count("\n") == 1


def test_decode_classifies_response():
    m = decode_message('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
    assert m.kind == "response"
    assert m.response is not None and m.response.id == 1
    assert m.response.result == {"ok": True}


def test_decode_classifies_notification_and_request():
    n = decode_message('{"jsonrpc":"2.0","method":"notifications/x"}')
    assert n.kind == "notification"
    r = decode_message('{"jsonrpc":"2.0","id":7,"method":"sampling/createMessage"}')
    assert r.kind == "request" and r.request is not None and r.request.id == 7


def test_decode_rejects_garbage():
    with pytest.raises(MCPError):
        decode_message("not json at all")


# ---------------------------------------------------------------------------
# Unit: naming
# ---------------------------------------------------------------------------

def test_qualify_and_split():
    q = qualify("my server", "do_thing")
    assert q == "my_server__do_thing"
    assert split_qualified(q) == ("my_server", "do_thing")
    assert split_qualified("noseparator") is None


def test_sanitize_strips_invalid_chars():
    assert sanitize("a/b c@d") == "a_b_c_d"


# ---------------------------------------------------------------------------
# Unit: config loading
# ---------------------------------------------------------------------------

def test_load_mcp_settings_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    settings = load_mcp_settings()
    assert settings.enabled is False
    assert settings.servers == []


def test_load_mcp_settings_parses_block(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "mcp:\n"
        "  enabled: true\n"
        "  servers:\n"
        "    - id: fs\n"
        "      transport: stdio\n"
        "      command: npx\n"
        "      args: ['-y', 'pkg']\n"
        "      tool_policy: auto\n",
        encoding="utf-8",
    )
    settings = load_mcp_settings()
    assert settings.enabled is True
    assert len(settings.servers) == 1
    s = settings.servers[0]
    assert s.server_id == "fs" and s.command == "npx"
    assert s.tool_policy == ToolPolicyKind.AUTO


def test_load_mcp_settings_rejects_duplicate_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "mcp:\n  servers:\n"
        "    - {id: dup, command: a}\n"
        "    - {id: dup, command: b}\n",
        encoding="utf-8",
    )
    with pytest.raises(MCPError):
        load_mcp_settings()


# ---------------------------------------------------------------------------
# Integration: session lifecycle over real stdio
# ---------------------------------------------------------------------------

def test_session_handshake_and_discovery():
    session = MCPClientSession(_cfg())
    try:
        hs = session.connect()
        assert hs.server_name == "fake"
        assert hs.capabilities.tools and hs.capabilities.resources and hs.capabilities.prompts
        tools = session.list_tools()
        assert [t.name for t in tools] == ["echo"]
        resources = session.list_resources()
        assert resources[0].uri == "mem://note"
        prompts = session.list_prompts()
        assert prompts[0].name == "greet"
    finally:
        session.close()


def test_session_call_tool():
    session = MCPClientSession(_cfg())
    try:
        session.connect()
        result = session.call_tool("echo", {"text": "hi"})
        assert not result.is_error
        assert result.joined_text() == "echo: hi"
    finally:
        session.close()


def test_session_read_resource_and_get_prompt():
    session = MCPClientSession(_cfg())
    try:
        session.connect()
        res = session.read_resource("mem://note")
        assert "resource body" in res.joined_text()
        prompt = session.get_prompt("greet", {"who": "Bob"})
        assert prompt.messages[0].content == "Hello Bob"
    finally:
        session.close()


def test_capability_guard_when_not_advertised():
    session = MCPClientSession(_cfg(extra_args=["--no-resources"]))
    try:
        session.connect()
        with pytest.raises(MCPError):
            session.list_resources()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Integration: manager + registry + adapter
# ---------------------------------------------------------------------------

def test_manager_connect_and_catalog():
    manager = MCPManager(settings=MCPSettings(enabled=True, servers=[_cfg()]))
    try:
        catalog = manager.connect_all()
        assert catalog.counts() == {"tools": 1, "resources": 1, "prompts": 1}
        assert catalog.resolve_tool("fake__echo") == ("fake", "echo")
    finally:
        manager.shutdown()


def test_manager_failure_isolation():
    good = _cfg(server_id="good")
    bad = MCPServerConfig(
        server_id="bad", transport=TransportKind.STDIO,
        command="definitely_not_a_real_command_xyz", args=[],
        connect_timeout=5.0, request_timeout=5.0,
    )
    manager = MCPManager(settings=MCPSettings(enabled=True, servers=[good, bad]))
    try:
        manager.connect_all()
        # good still works; bad is isolated in connect_errors.
        assert manager.catalog.resolve_tool("good__echo") == ("good", "echo")
        assert "bad" in manager.connect_errors
        statuses = {s.server_id: s for s in manager.statuses()}
        assert statuses["good"].state == "ready"
        assert statuses["bad"].state == "failed"
    finally:
        manager.shutdown()


def test_register_into_tool_registry_and_dispatch():
    manager = MCPManager(settings=MCPSettings(enabled=True, servers=[_cfg()]))
    try:
        manager.connect_all()
        registry = ToolRegistry()
        n = register_mcp_tools(registry, manager)
        assert n == 1
        assert "fake__echo" in registry.names()
        call = ToolCall(id="c1", name="fake__echo", arguments={"text": "world"})
        result = registry.dispatch(call, _ctx())
        assert not result.is_error
        assert result.content == "echo: world"
    finally:
        manager.shutdown()


def test_approval_policy_denies_when_user_says_no():
    manager = MCPManager(
        settings=MCPSettings(enabled=True, servers=[_cfg(policy=ToolPolicyKind.APPROVE)])
    )
    try:
        manager.connect_all()
        registry = ToolRegistry()
        register_mcp_tools(registry, manager)
        call = ToolCall(id="c2", name="fake__echo", arguments={"text": "x"})
        result = registry.dispatch(call, _ctx(approve=auto_no))
        assert result.is_error
        assert "拒绝" in result.content
    finally:
        manager.shutdown()


def test_deny_policy_skips_registration():
    manager = MCPManager(
        settings=MCPSettings(enabled=True, servers=[_cfg(policy=ToolPolicyKind.DENY)])
    )
    try:
        manager.connect_all()
        registry = ToolRegistry()
        n = register_mcp_tools(registry, manager)
        assert n == 0
        assert registry.names() == []
    finally:
        manager.shutdown()
