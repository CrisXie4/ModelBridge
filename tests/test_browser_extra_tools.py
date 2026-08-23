"""New browser tools (2026-08-23) — scroll / net / press_key / get_cookies / inject_js.

Covers:
- registry contents (panel gets the full set incl. inject_js)
- page_scroll & net_* relay without any approval prompt
- press_key shares the browser_write "always" group with click/fill/navigate
- get_cookies keeps an independent gate: never inherits the group, never
  passes auto=True (credential reads are never LLM-auto-approved)

Calls go through ``registry.dispatch`` so the tests exercise the same path
the agent loop uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from modelbridge.agent.context import AgentContext, ApprovalDecision
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.base import ToolCall
from modelbridge.agent.tools.browser_tools import build_browser_registry


class _FakeBridge:
    def __init__(self):
        self.calls = []

    def call(self, name, args, *, timeout=None):  # noqa: ARG002
        self.calls.append(name)
        return {"ok": True, "content": f"did {name}"}


def _ctx(approve, bridge):
    return AgentContext(
        policy=PathPolicy(allowed_dirs=[], blocked_patterns=[]),
        cwd=Path.cwd(),
        approve=approve,
        browser_bridge=bridge,
    )


def _run(reg, name, kwargs, ctx):
    """dispatch with JSON-round-tripped args (the loop passes decoded JSON)."""
    call = ToolCall(id=f"c-{name}", name=name,
                    arguments=json.loads(json.dumps(kwargs)))
    return reg.dispatch(call, ctx)


def _no_ask(tool, **_kw):  # pragma: no cover - must never run
    raise AssertionError(f"unexpected approval prompt: {tool}")


# ---------------------------------------------------------------------------
# Registry contents
# ---------------------------------------------------------------------------

def test_registry_has_full_toolset_for_panel():
    names = set(build_browser_registry(include_write=True).tools)
    expected = {
        "read_page", "get_selection", "query_dom", "extract",
        "page_scroll", "net_start", "net_log", "net_stop",
        "click", "fill", "navigate", "press_key", "inject_js", "get_cookies",
    }
    assert expected <= names


def test_registry_read_only_excludes_gated_tools():
    names = set(build_browser_registry(include_write=False).tools)
    assert "page_scroll" in names and "net_log" in names
    assert "click" not in names and "inject_js" not in names and "get_cookies" not in names


def test_inject_js_same_class_in_both_registries():
    from modelbridge.agent.tools.computer_control_tools import (
        InjectJsTool,
        build_computer_registry,
    )

    reg = build_browser_registry(include_write=True)
    assert reg.tools["inject_js"].__class__ is InjectJsTool
    assert "inject_js" in build_computer_registry().tools


# ---------------------------------------------------------------------------
# Read tools: no approval ever
# ---------------------------------------------------------------------------

def test_page_scroll_and_net_tools_relay_without_approval():
    bridge = _FakeBridge()
    ctx = _ctx(_no_ask, bridge)
    reg = build_browser_registry(include_write=False)

    cases = [
        ("page_scroll", {"dy": 600}),
        ("net_start", {}),
        ("net_log", {"filter": "api"}),
        ("net_stop", {}),
    ]
    for name, kwargs in cases:
        result = _run(reg, name, kwargs, ctx)
        assert not result.is_error, (name, result.content)
    assert bridge.calls == [c[0] for c in cases]


# ---------------------------------------------------------------------------
# press_key: shares the browser_write always-group
# ---------------------------------------------------------------------------

def _always_approve(*, tool, **_kw):  # noqa: ARG001
    return ApprovalDecision.ALWAYS


def test_press_key_shares_browser_write_group():
    bridge = _FakeBridge()
    ctx = _ctx(_always_approve, bridge)
    reg = build_browser_registry(include_write=True)
    r1 = _run(reg, "fill", {"selector": "#q", "value": "x", "reason": "搜索"}, ctx)
    r2 = _run(reg, "press_key", {"key": "Enter", "reason": "提交"}, ctx)
    assert not r1.is_error and not r2.is_error


def test_press_key_schema_has_trusted_flag():
    schema = build_browser_registry(include_write=True).tools["press_key"].json_schema()
    assert schema["properties"]["trusted"]["type"] == "boolean"
    assert set(schema["required"]) == {"key", "reason"}


# ---------------------------------------------------------------------------
# get_cookies: independent gate, never auto-approved
# ---------------------------------------------------------------------------

def test_get_cookies_does_not_inherit_browser_write_group():
    asks = {"n": 0}

    def approve(**_kw):
        asks["n"] += 1
        return ApprovalDecision.ALWAYS

    ctx = _ctx(approve, _FakeBridge())
    reg = build_browser_registry(include_write=True)
    r1 = _run(reg, "fill", {"selector": "#q", "value": "x", "reason": "搜索"}, ctx)
    r2 = _run(reg, "get_cookies", {"reason": "排查登录问题"}, ctx)
    assert not r1.is_error and not r2.is_error
    assert asks["n"] == 2  # cookie 读不认 fill 的 ALWAYS，重新问了


def test_get_cookies_never_asks_for_auto_approval():
    seen = {"auto": None}

    def approve(*, auto=False, **_kw):
        seen["auto"] = auto
        return ApprovalDecision.YES

    ctx = _ctx(approve, _FakeBridge())
    reg = build_browser_registry(include_write=True)
    r = _run(reg, "get_cookies", {"reason": "排查登录问题"}, ctx)
    assert not r.is_error
    assert seen["auto"] is False  # 即使 /auto 模式也必须人工确认


def test_get_cookies_denied_returns_error():
    def approve(**_kw):
        return ApprovalDecision.NO

    bridge = _FakeBridge()
    ctx = _ctx(approve, bridge)
    reg = build_browser_registry(include_write=True)
    r = _run(reg, "get_cookies", {"reason": "看看"}, ctx)
    assert r.is_error
    assert bridge.calls == []  # 拒绝后不碰浏览器
