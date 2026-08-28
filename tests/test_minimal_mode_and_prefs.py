"""极简模式 + skills 启停 + web ui-prefs 路由 — behaviour tests."""

from __future__ import annotations

from modelbridge.agent.tools.registry import build_default_registry
from modelbridge.agent.tools.skill_tool import UseSkillTool
from modelbridge.skills.prefs import load_disabled


def test_minimal_registry_allowlist():
    reg = build_default_registry(include_bash=True, minimal=True)
    assert set(reg.tools) == set(("read_file", "write_file", "run_bash"))


def test_minimal_registry_without_bash():
    reg = build_default_registry(minimal=True)
    assert set(reg.tools) == set(("read_file", "write_file"))
    # 模式外：常规注册表不受影响（对照）。
    full = build_default_registry(include_bash=True)
    assert set(("grep", "list_dir", "str_replace")) <= set(full.tools)


def test_minimal_system_prompt_is_terse_and_scoped():
    from modelbridge.cli import _minimal_system_prompt

    text = _minimal_system_prompt(allow_bash=True)
    assert "Verify your work by running the code or tests." in text
    assert "Keep answers brief and factual" in text
    for tool in ("read_file", "write_file", "run_bash"):
        assert tool in text
    # 不给模型介绍已裁掉的编辑流。
    assert "str_replace" not in text


def test_minimal_prompt_is_terse():
    from modelbridge.cli import _minimal_system_prompt

    text = _minimal_system_prompt(allow_bash=True)
    assert text.count("\n") < 18  # 寡言：全文比常规版短一个量级


# ---------------------------------------------------------------------------
# skills prefs
# ---------------------------------------------------------------------------

def test_skill_prefs_roundtrip(tmp_path):
    from modelbridge.skills.prefs import save_disabled

    p = tmp_path / "skills.json"
    save_disabled(("beta",), path=p)
    assert load_disabled(p) == set(("beta",))
    current = load_disabled(p)
    current.discard("beta")
    save_disabled(current, path=p)
    assert not load_disabled(p)
    assert load_disabled(tmp_path / "missing.json") == set()


def test_use_skill_rejects_disabled():
    from modelbridge.agent.context import AgentContext
    from modelbridge.agent.security import PathPolicy
    from pathlib import Path as _P

    tool = UseSkillTool(disabled=set(("old-flow",)))
    ctx = AgentContext(policy=PathPolicy([], []), cwd=_P.cwd())
    res = tool.execute(dict(name="old-flow"), ctx)
    assert res.is_error and "禁用" in (res.content or "")


# ---------------------------------------------------------------------------
# webui ui-prefs routes (direct calls, no HTTP layer)
# ---------------------------------------------------------------------------

def _route_module(monkeypatch, tmp_path):
    import modelbridge.web.routers.ui_prefs as mod

    monkeypatch.setattr(
        "modelbridge.agent.ui_prefs.get_app_dir", lambda: tmp_path, raising=False
    )
    return mod


def test_ui_prefs_router_roundtrip(monkeypatch, tmp_path):
    mod = _route_module(monkeypatch, tmp_path)
    first = mod.get_ui_prefs()
    assert "rail_ratio" in first["prefs"]

    from modelbridge.web.routers.ui_prefs import UiPrefsUpdate

    vals = dict(rail_ratio=0.3)
    out = mod.update_ui_prefs(UiPrefsUpdate(values=vals))
    assert out["saved"] is True and out["errors"] == {}
    assert out["prefs"]["rail_ratio"] == 0.3
    again = mod.get_ui_prefs()
    assert again["prefs"]["rail_ratio"] == 0.3

    bad_vals = dict(nope=1, color_bad="red")
    bad = mod.update_ui_prefs(UiPrefsUpdate(values=bad_vals))
    assert bad["saved"] is False
    assert set(bad["errors"]) == set(("nope", "color_bad"))

    reset_out = mod.update_ui_prefs(UiPrefsUpdate(values=dict(), reset=True))
    assert reset_out["saved"] is True and reset_out["prefs"]["user_align"] == "right"
