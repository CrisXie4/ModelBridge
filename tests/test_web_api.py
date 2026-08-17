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
