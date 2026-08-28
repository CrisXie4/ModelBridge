"""Webui MCP 管理 + Skills 启停 — backend behaviour (isolated MBRIDGE_HOME)."""

from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("MBRIDGE_HOME", str(home_dir))
    return home_dir


def _seed_config(home, servers):
    lines = ["mcp:", "  enabled: true", "  servers:"]
    for sid, cmd in servers:
        lines.append(f"    - server_id: {sid}")
        lines.append(f"      command: {cmd}")
        lines.append("      args: []")
        lines.append("      enabled: true")
    (home / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def test_skills_list_carries_enabled_flag():
    from modelbridge.web.routers.skills import list_skills

    out = list_skills()
    assert isinstance(out["skills"], list)
    for entry in out["skills"]:
        assert entry["enabled"] is True  # 干净环境下默认全启用


def test_skill_toggle_persists_and_reflects(home):
    from modelbridge.web.routers.skills import list_skills, toggle_skill

    first = list_skills()["skills"][0]
    name = first["name"]

    res = toggle_skill(name, type("P", (), {"enabled": False})())
    assert res == {"name": name, "enabled": False}
    # 列表立即反映禁用态。
    after = next(s for s in list_skills()["skills"] if s["name"] == name)
    assert after["enabled"] is False
    toggle_skill(name, type("P", (), {"enabled": True})())
    restored = next(s for s in list_skills()["skills"] if s["name"] == name)
    assert restored["enabled"] is True


# ---------------------------------------------------------------------------
# MCP 持久启停
# ---------------------------------------------------------------------------

def test_mcp_toggle_roundtrip(home):
    from modelbridge.mcp.config import load_mcp_settings, set_server_enabled
    from modelbridge.web.routers.mcp import list_servers

    _seed_config(home, [("alpha", "uvx"), ("beta", "node")])

    listed = list_servers()
    assert {s["server_id"] for s in listed["servers"]} == {"alpha", "beta"}
    assert listed["enabled"] is True

    assert set_server_enabled("alpha", False) is True
    after = {c.server_id: c.enabled for c in load_mcp_settings().servers}
    assert after == {"alpha": False, "beta": True}

    # 未知 id 返回 False（路由层据此 404）。
    assert set_server_enabled("ghost", True) is False


def test_toggle_via_router_endpoint(home):
    from fastapi import HTTPException

    from modelbridge.web.routers.mcp import list_servers, toggle_server

    _seed_config(home, [("only", "cmd")])
    payload = type("P", (), {"enabled": False})()
    out = toggle_server("only", payload)
    assert out == {"ok": True, "enabled": False}
    assert list_servers()["servers"][0]["enabled"] is False

    try:
        toggle_server("missing", payload)
        raised = False
    except HTTPException as e:
        raised = e.status_code == 404
    assert raised
