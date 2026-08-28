"""Bridge ↔ panel UI-preference plumbing + status snapshot extras.

* protocol builders for ``ui_prefs_get`` / ``ui_prefs_set`` / ``ui_prefs``
  round-trip through the framing layer.
* ``state`` carries ``bgs`` alongside todos.
* Host routes preference frames: set applies+persists, per-key errors come
  back, reset restores defaults, ready carries the initial bag.
* UiPrefs.maybe_reload() hot-reloads when another process touches ui.json.
"""

from __future__ import annotations

import io
import os

from modelbridge.agent.ui_prefs import UiPrefs
from modelbridge.bridge import protocol as P
from modelbridge.bridge.host import Host


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def test_pref_builders_round_trip():
    wire = P.encode_message(P.ui_prefs_set(id="t1", values={"rail_ratio": 0.3}))
    msg = P.read_message(io.BytesIO(wire))
    assert msg["type"] == P.T_UI_PREFS_SET
    assert msg["values"] == {"rail_ratio": 0.3}

    reply = P.ui_prefs(
        id="t1", prefs={"user_align": "right"}, saved=False, errors={"x": "坏值"},
    )
    back = P.read_message(io.BytesIO(P.encode_message(reply)))
    assert back["type"] == P.T_UI_PREFS and back["errors"] == {"x": "坏值"}
    assert P.read_message(io.BytesIO(P.encode_message(P.ui_prefs_get())))["type"] == P.T_UI_PREFS_GET


def test_state_carries_bgs():
    frame = P.state(id="s1", todos=[{"content": "a", "status": "pending"}],
                    bgs=[{"id": "b1", "status": "running", "command": "pytest"}])
    assert frame["bgs"][0]["id"] == "b1"
    assert P.state(id="s1")["bgs"] == []


def test_ready_includes_initial_prefs():
    r = P.ready(version="9", models=[], default_model=None, prefs={"color_accent": "#58a6ff"})
    assert r["uiPrefs"]["color_accent"] == "#58a6ff"


# ---------------------------------------------------------------------------
# Host routing
# ---------------------------------------------------------------------------

class _Collect:
    def __init__(self):
        self.frames: list[dict] = []

    def __call__(self, msg):
        self.frames.append(msg)

    def last(self):
        return self.frames[-1]


def _host_with_prefs(tmp_path) -> tuple[Host, _Collect]:
    host = Host(io.BytesIO(), io.BytesIO())
    collector = _Collect()
    host.send = collector  # type: ignore[method-assign]
    host._ui_prefs = UiPrefs(path=tmp_path / "ui.json")
    return host, collector


def test_host_set_applies_and_persists(tmp_path):
    host, sent = _host_with_prefs(tmp_path)
    host._on_ui_prefs_set({"id": "r1", "values": {"rail_ratio": 0.32, "show_tasks": False}})
    reply = sent.last()
    assert reply["saved"] is True and reply["errors"] == {}
    assert reply["prefs"]["rail_ratio"] == 0.32
    assert reply["prefs"]["show_tasks"] is False
    # 落盘：新实例从文件读回一致。
    again = UiPrefs.load(path=host._ui_prefs.path)
    assert again.rail_ratio == 0.32 and again.show_tasks is False


def test_host_reports_per_key_errors(tmp_path):
    host, sent = _host_with_prefs(tmp_path)
    host._on_ui_prefs_set({"id": "", "values": {"nope": 1, "color_bad": "red", "footer_hint": False}})
    reply = sent.last()
    assert reply["saved"] is False
    assert set(reply["errors"]) == {"nope", "color_bad"}
    assert reply["prefs"]["footer_hint"] is False  # 合法键仍然生效


def test_host_reset_restores_defaults_and_keeps_path(tmp_path):
    host, sent = _host_with_prefs(tmp_path)
    host._on_ui_prefs_set({"id": "", "values": {"user_align": "left"}})
    host._on_ui_prefs_set({"id": "", "reset": True})
    reply = sent.last()
    assert reply["saved"] is True
    assert reply["prefs"]["user_align"] == "right"
    assert host._ui_prefs.path == tmp_path / "ui.json"


def test_host_get_returns_current_bag(tmp_path):
    host, sent = _host_with_prefs(tmp_path)
    host._on_ui_prefs_get({"id": ""})
    assert sent.last()["type"] == P.T_UI_PREFS
    assert "rail_ratio" in sent.last()["prefs"]


# ---------------------------------------------------------------------------
# Hot reload (panel edits ui.json → terminal TUI picks it up)
# ---------------------------------------------------------------------------

def test_maybe_reload_detects_external_change(tmp_path):
    p = UiPrefs(path=tmp_path / "ui.json")
    p.save()
    before = p.rail_ratio

    ext = UiPrefs.load(path=p.path)
    ext.rail_ratio = 0.5
    ext.save()
    # 显式顶高 mtime，避免 Windows 文件系统时间粒度导致同刻写入。
    st = os.stat(p.path)
    os.utime(p.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    assert p.maybe_reload() is True
    assert p.rail_ratio == 0.5 and before != 0.5
    # 第二次无变化 → 不重载。
    assert p.maybe_reload() is False
