"""``/ui`` command + :class:`UiPrefs` — user-customisable TUI look.

* Defaults, clamping, coercion errors (bool / ratio / hex / enum).
* save → load round-trip on an isolated ``ui.json``.
* Corrupt / unknown fields degrade to defaults instead of raising.
* Rail honours section toggles and the footer-hint switch.
* The command handler wires into SlashContext.ui_prefs and fires
  ``on_ui_change`` so a live TUI repaints immediately.
"""

from __future__ import annotations

from rich.console import Console

from modelbridge.agent.commands import SlashContext, handle_slash
from modelbridge.agent.tui import TuiRepl
from modelbridge.agent.ui import SidePanel
from modelbridge.agent.ui_prefs import UiPrefs


def _prefs(tmp_path) -> UiPrefs:
    return UiPrefs(path=tmp_path / "ui.json")


# ---------------------------------------------------------------------------
# UiPrefs core
# ---------------------------------------------------------------------------

def test_defaults_and_clamp():
    p = UiPrefs()
    p.rail_ratio = 9.0
    p.rail_max = 5
    p.clamp()
    assert p.rail_ratio == 0.55
    assert 40 <= p.rail_max <= 96


def test_set_key_bool_enum_hex(tmp_path):
    p = _prefs(tmp_path)
    p.set_key("show_tasks", "off")
    assert p.show_tasks is False
    p.set_key("show_tasks", "on")
    assert p.show_tasks is True
    p.set_key("user_align", "left")
    assert p.user_align == "left"
    p.set_key("color_bad", "#FF7B72")
    assert p.color_bad == "#ff7b72"


def test_set_key_rejects_garbage(tmp_path):
    p = _prefs(tmp_path)
    for key, val in (
        ("show_tasks", "maybe"),      # bool
        ("rail_ratio", "not-a-num"),  # float
        ("color_accent", "red"),      # hex
        ("user_align", "center"),     # enum
    ):
        try:
            p.set_key(key, val)
            raised = False
        except (ValueError, TypeError):
            raised = True
        assert raised, f"{key}={val} 应被拒绝"


def test_set_key_unknown_key():
    try:
        UiPrefs().set_key("no_such_knob", "1")
        raised = False
    except KeyError:
        raised = True
    assert raised


def test_save_load_roundtrip(tmp_path):
    p = _prefs(tmp_path)
    p.set_key("rail_ratio", 0.32)
    p.set_key("footer_hint", False)
    p2 = UiPrefs.load(path=p.path)
    assert p2.rail_ratio == 0.32
    assert p2.footer_hint is False


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    bad = tmp_path / "ui.json"
    bad.write_text("{ not json !!!", encoding="utf-8")
    p = UiPrefs.load(path=bad)
    assert p == UiPrefs()


def test_unknown_fields_ignored_bad_values_skipped(tmp_path):
    f = tmp_path / "ui.json"
    f.write_text(
        '{"frobnicate": true, "rail_ratio": 99, "footer_hint": false}',
        encoding="utf-8",
    )
    p = UiPrefs.load(path=f)
    assert p.footer_hint is False          # 合法值生效
    assert p.rail_ratio == 0.55           # 越界值经 clamp 兜住（float(raw) 成功）
    assert not hasattr(p, "frobnicate")


# ---------------------------------------------------------------------------
# Rail integration
# ---------------------------------------------------------------------------

def _tui_with(prefs: UiPrefs) -> TuiRepl:
    tui = TuiRepl(side_panel=SidePanel(cwd="D:/proj"), prefs=prefs)
    tui.side_panel.begin_turn("ds")
    tui._term_w, tui._term_h = 120, 30
    return tui


def test_rail_section_toggles():
    off = _tui_with(UiPrefs(show_tasks=False, show_bg=False))
    text = "".join(seg for _s, seg in off._rail_fragments())
    assert "任务" not in text and "状态" in text

    t = _tui_with(UiPrefs(footer_hint=False))
    text = "".join(seg for _s, seg in t._rail_fragments())
    assert "PgUp/PgDn" not in text


def test_rail_colors_follow_prefs():
    tui = _tui_with(UiPrefs(color_accent="#00ff00"))
    styles = {s for s, _seg in tui._rail_fragments() if s}
    assert any("#00ff00" in s for s in styles)


def test_rail_width_honours_ratio():
    tui = _tui_with(UiPrefs(rail_ratio=0.25, rail_max=72))
    tui._term_w = 200
    assert tui._rail_w() == 50   # 200 * 0.25


# ---------------------------------------------------------------------------
# /ui command
# ---------------------------------------------------------------------------

def _sctx(prefs: UiPrefs, fired: list[int]):
    console = Console(record=True, width=100)
    return (
        SlashContext(
            console=console,
            session=None,
            agent_ctx=None,
            registry=None,
            model_name="dummy",
            entry=None,
            thinking_state={},
            ui_prefs=prefs,
            on_ui_change=lambda: fired.append(1),
        ),
        console,
    )


def test_ui_lists_all_knobs(tmp_path):
    prefs = _prefs(tmp_path)
    sctx, console = _sctx(prefs, [])
    handle_slash("/ui", sctx)
    out = console.export_text()
    for knob in ("rail_ratio", "show_tasks", "color_accent", "user_align"):
        assert knob in out
    assert "ui.json" in out


def test_ui_set_applies_persists_and_fires_callback(tmp_path):
    prefs = _prefs(tmp_path)
    fired: list[int] = []
    sctx, console = _sctx(prefs, fired)
    handle_slash("/ui rail_ratio 0.3", sctx)
    out = console.export_text()
    assert prefs.rail_ratio == 0.3
    assert fired == [1]
    assert "✓" in out and "已保存" in out
    assert UiPrefs.load(path=prefs.path).rail_ratio == 0.3


def test_ui_unknown_key_reports_without_raising(tmp_path):
    sctx, console = _sctx(_prefs(tmp_path), [])
    handle_slash("/ui nope on", sctx)
    assert "未知配置项" in console.export_text()


def test_ui_reset_restores_defaults(tmp_path):
    prefs = _prefs(tmp_path)
    prefs.set_key("user_align", "left")
    fired: list[int] = []
    sctx, _console = _sctx(prefs, fired)
    handle_slash("/ui reset", sctx)
    assert prefs.user_align == "right"
    assert fired == [1]
