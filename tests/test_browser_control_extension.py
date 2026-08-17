"""Browser control extension — reason field, trusted flag, judge wiring.

Covers the 2026-08-11 design:
- click/fill/navigate require a ``reason`` param (shown on approval card)
- click has a ``trusted`` flag (CDP hard path vs soft DOM-event simulation)
- protocol.approval frame carries ``reason`` to the extension
- session_runner's LLM judge fails open (no model → fall through, not crash)
"""

from __future__ import annotations

from modelbridge.agent.tools.browser_write_tools import ClickTool, FillTool, NavigateTool
from modelbridge.agent.tools.computer_control_tools import InjectJsTool
from modelbridge.bridge import protocol as P


# ---------------------------------------------------------------------------
# reason / trusted in tool schemas
# ---------------------------------------------------------------------------

def test_click_schema_requires_reason_and_offers_trusted():
    schema = ClickTool().json_schema()
    assert "reason" in schema["required"]
    assert "selector" in schema["required"]
    assert schema["properties"]["trusted"]["type"] == "boolean"
    assert schema["properties"]["trusted"]["default"] is False


def test_fill_schema_requires_reason():
    schema = FillTool().json_schema()
    assert "reason" in schema["required"]
    assert {"selector", "value", "reason"} == set(schema["required"])


def test_navigate_schema_requires_reason():
    schema = NavigateTool().json_schema()
    assert "reason" in schema["required"]
    assert {"url", "reason"} == set(schema["required"])


def test_inject_js_schema_requires_reason():
    schema = InjectJsTool().json_schema()
    assert "reason" in schema["required"]
    assert {"code", "reason"} == set(schema["required"])


# ---------------------------------------------------------------------------
# _approval surfaces reason + trusted mode
# ---------------------------------------------------------------------------

def test_click_approval_surfaces_trusted_mode():
    t = ClickTool()
    soft = t._approval({"selector": "#btn", "reason": "提交", "trusted": False})
    assert soft[2] == "提交"  # (summary, detail, reason)
    assert "模拟点击" in soft[1]

    hard = t._approval({"selector": "#btn", "reason": "提交", "trusted": True})
    assert hard[2] == "提交"
    assert "CDP 真实输入" in hard[1]


def test_fill_approval_carries_reason():
    t = FillTool()
    summary, detail, reason = t._approval({"selector": "#q", "value": "x", "reason": "搜索"})
    assert reason == "搜索"
    assert "selector: #q" in detail


def test_navigate_approval_carries_reason():
    t = NavigateTool()
    _, _, reason = t._approval({"url": "https://x.io", "reason": "打开站点"})
    assert reason == "打开站点"


# ---------------------------------------------------------------------------
# protocol.approval frame carries reason
# ---------------------------------------------------------------------------

def test_approval_frame_includes_reason():
    frame = P.approval(id="t1", request_id="r1", tool="click",
                       summary="点击", detail="sel", reason="清缓存")
    assert frame["type"] == P.T_APPROVAL
    assert frame["reason"] == "清缓存"
    assert frame["tool"] == "click"


def test_approval_frame_reason_defaults_empty():
    frame = P.approval(id="t1", request_id="r1", tool="click", summary="x")
    assert frame["reason"] == ""


# ---------------------------------------------------------------------------
# session_runner._llm_safety_judge fails open (graceful, never raises)
# ---------------------------------------------------------------------------

def test_llm_safety_judge_fails_open_when_no_model(monkeypatch):
    """No tiny model + no default → (False, reason) — must NOT raise."""
    from modelbridge.bridge import session_runner as sr

    class _EmptyModels:
        models = []

    class _EmptyCfg:
        default_model = None

    monkeypatch.setattr("modelbridge.config.load_app_config", lambda: _EmptyCfg())
    monkeypatch.setattr("modelbridge.config.load_models_file", lambda: _EmptyModels())
    monkeypatch.setattr("modelbridge.client.find_model", lambda _n: None)

    is_safe, reason = sr._llm_safety_judge(
        tool="click", summary="点击", detail="sel", reason="清缓存",
    )
    assert is_safe is False
    assert reason  # informative fall-through reason, not silent


def test_llm_safety_judge_catches_exceptions(monkeypatch):
    """Provider blowing up must not propagate — judge returns (False, '')."""
    from modelbridge.bridge import session_runner as sr

    class _BoomCfg:
        default_model = None

    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    # load_app_config itself raises — the try/except must swallow it.
    monkeypatch.setattr("modelbridge.config.load_app_config", _boom)

    is_safe, reason = sr._llm_safety_judge(
        tool="click", summary="x", detail="y", reason="z",
    )
    assert is_safe is False
    assert reason  # informative failure reason, swallowed exception
    _ = _BoomCfg  # silence unused warning
