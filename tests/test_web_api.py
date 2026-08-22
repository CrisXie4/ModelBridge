"""Web backend API smoke tests (FastAPI TestClient)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from fastapi.testclient import TestClient  # noqa: E402

from modelbridge.web import create_app  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    return tmp_path


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_skills_lists_builtins(client, home):
    r = client.get("/api/skills")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["skills"]}
    assert "systematic-debugging" in names
    assert "tdd" in names


def test_skill_detail(client, home):
    r = client.get("/api/skills/tdd")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "tdd"
    assert body["scope"] == "builtin"
    assert "红" in body["body"]  # RED-GREEN cycle content


def test_model_crud(client, home):
    # create
    r = client.post(
        "/api/models",
        json={
            "name": "chan-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "provider": "deepseek",
            "api_key": "sk-test123",
            "level": "cheap",
        },
    )
    assert r.status_code == 200
    assert r.json()["name"] == "chan-test"
    assert r.json()["has_api_key"] is True
    # api_key is never exposed in the response
    assert "sk-test123" not in r.text

    # list
    r = client.get("/api/models")
    assert r.status_code == 200
    assert any(m["name"] == "chan-test" for m in r.json()["models"])

    # delete
    r = client.delete("/api/models/chan-test")
    assert r.status_code == 200


def test_config_get_update(client, home):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert "routing_mode" in r.json()

    r = client.put(
        "/api/config",
        json={
            "default_model": None,
            "routing_mode": "powerful",
            "levels": {"cheap": None},
            "profiles": {},
            "active_profile": None,
        },
    )
    assert r.status_code == 200
    assert r.json()["routing_mode"] == "powerful"


def test_prompts_get(client, home):
    r = client.get("/api/prompts")
    assert r.status_code == 200
    data = r.json()
    assert "system" in data
    assert "rules" in data
    assert len(data["system"]) > 0


def test_cost_estimate_includes_fixed_prompt_prefix(client, home):
    """/usage/cost must bill the stable prefix (system.md + rules.md) that
    every real request carries — not just the pasted user text."""
    (home / "models.yaml").write_text(
        "models:\n"
        "  - name: ds\n"
        "    provider: deepseek\n"
        "    base_url: https://api.deepseek.com\n"
        "    model: deepseek-v4-flash\n",
        encoding="utf-8",
    )
    r = client.post("/api/usage/cost", json={"model": "ds", "prompt": "帮我看看这段代码"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Fresh home → built-in default system prompt forms the fixed prefix.
    assert body["prefix_tokens"] > 0
    assert body["user_tokens"] > 0
    assert body["input_tokens"] == body["prefix_tokens"] + body["user_tokens"]

    # A real user system.md takes over the prefix (same loading rules as
    # PromptBuilder) — much longer file → visibly more prefix tokens.
    (home / "system.md").write_text(
        "# 自定义规范\n" + "每条规范都要遵守，违反即重写。" * 400,
        encoding="utf-8",
    )
    r2 = client.post("/api/usage/cost", json={"model": "ds", "prompt": "帮我看看这段代码"})
    assert r2.json()["prefix_tokens"] > body["prefix_tokens"]
    # User text side is unaffected by the prefix swap.
    assert r2.json()["user_tokens"] == body["user_tokens"]


def test_cache_stats(client, home):
    from modelbridge.cache import record_hit

    record_hit(saved_tokens=10, saved_cost=0.001, model="ds-flash")
    r = client.get("/api/usage/cache")
    assert r.status_code == 200
    body = r.json()
    assert "hits" in body
    # Per-model cache-domain table (each provider+model is its own domain).
    assert body["per_model"]["ds-flash"]["hits"] == 1
    assert body["per_model"]["ds-flash"]["hit_rate"] == 1.0
    # Billable-input accounting (prompt tokens billed + what they cost).
    assert body["billed_tokens"] == 0  # legacy record_hit call priced nothing
    assert "spend" in body and "billed_tokens" in body["per_model"]["ds-flash"]


def test_webui_clean_urls(client, home):
    """Direct visits / refreshes of page routes must serve the flat
    ``<page>.html`` export files (regression: Starlette's html=True only
    maps directories → index.html, so /usage used to 404 outside of
    client-side navigation)."""
    from modelbridge.web.server import _find_webui_dir

    if _find_webui_dir() is None:
        pytest.skip("webui/out static export not built")
    assert client.get("/usage").status_code == 200
    assert client.get("/usage.html").status_code == 200
    assert client.get("/").status_code == 200
    # Unknown pages keep 404 (serving the export's own 404.html).
    assert client.get("/definitely-not-a-page").status_code == 404
    # API still wins over the static mount.
    assert client.get("/api/usage/cache").status_code == 200


def test_model_doctor_endpoint(client, home, monkeypatch):
    """Regression: the router used to pass the model NAME string to
    ``run_model_doctor`` (expects a ModelEntry) → 500 on every test click."""
    (home / "models.yaml").write_text(
        "models:\n"
        "  - name: ds\n"
        "    provider: custom\n"
        "    base_url: https://invalid.example.test/v1\n"
        "    model: x\n",
        encoding="utf-8",
    )

    import modelbridge.doctor as doctor_mod

    class _StubProvider:
        name = "custom"

        def __init__(self, entry):
            self.entry = entry

        def health_check(self, *, timeout=5.0):
            return True, "stub ok"

        def chat(self, req, *, timeout=60.0, save_raw=False, verbose_label=""):
            from modelbridge.schemas import ChatResponse

            return ChatResponse(content="pong", elapsed_ms=12)

    monkeypatch.setattr(doctor_mod, "get_provider", lambda e: _StubProvider(e))

    r = client.post("/api/doctor/ds")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ds"
    assert "chat_ok" in body and "results" in body

    # Unknown model → clean 404, not a 500.
    assert client.post("/api/doctor/no-such").status_code == 404


def test_models_endpoint_default_capabilities_is_model(client, home):
    """Regression: ModelIn.capabilities default was accidentally a raw
    ConfigDict dict — POST without the block must yield a real object."""
    r = client.post(
        "/api/models",
        json={
            "name": "cap-test",
            "base_url": "https://invalid.example.test/v1",
            "model": "x",
        },
    )
    assert r.status_code == 200
    assert r.json()["capabilities"]["tools"] is False


def test_missing_webui_build_warns_at_startup(home, monkeypatch, capsys):
    """A server started while webui/out is missing (e.g. mid-rebuild —
    ``next build``'s export phase empties out/ first) must say so loudly
    instead of silently 404ing every page."""
    monkeypatch.setattr(
        "modelbridge.web.server._find_webui_dir", lambda: None
    )
    from modelbridge.web import create_app

    create_app()
    err = capsys.readouterr().err
    assert "webui/out" in err
    assert "build:static" in err
