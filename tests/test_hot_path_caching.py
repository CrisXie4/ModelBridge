# tests/test_hot_path_caching.py
"""Behaviour of the mtime-keyed hot-path caches (perf regression guards).

The caches exist because the REPL hot path re-read config.yaml /
models.yaml / pricing.yaml several times per agent turn (measured ~18ms
per models.yaml parse) and construct a provider (keyring hit) per call.
These tests pin the *correctness* side: cache hits must return isolated
deep copies, and any real change — our own save or an external edit —
must be picked up on the next load.
"""

from __future__ import annotations

import json

import pytest
import yaml

from modelbridge.config import (
    find_model,
    load_app_config,
    load_models_file,
    models_generation,
)
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers.base import _shared_http_client
from modelbridge.providers.registry import get_provider


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    return tmp_path


def _write(path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _touch_rewrite(path) -> None:
    """Rewrite with different bytes so (mtime, size) definitely changes."""
    st = path.read_text(encoding="utf-8")
    path.write_text(st + "\n# touched\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# AppConfig cache
# ---------------------------------------------------------------------------

def test_config_cache_returns_isolated_copies(home):
    (home / "config.yaml").exists() or _write(home / "config.yaml", {"default_model": "a"})
    cfg = load_app_config()
    cfg.default_model = "mutated"
    assert load_app_config().default_model == "a"


def test_config_external_edit_invalidates(home):
    _write(home / "config.yaml", {"default_model": "a"})
    assert load_app_config().default_model == "a"
    _write(home / "config.yaml", {"default_model": "b"})
    assert load_app_config().default_model == "b"


def test_config_save_invalidates(home):
    _write(home / "config.yaml", {"default_model": "a"})
    cfg = load_app_config()
    cfg.default_model = "saved"
    from modelbridge.config import save_app_config

    save_app_config(cfg)
    assert load_app_config().default_model == "saved"


# ---------------------------------------------------------------------------
# ModelsFile cache
# ---------------------------------------------------------------------------

def test_models_cache_returns_isolated_entries(home):
    _write(home / "models.yaml", {
        "models": [{"name": "m", "provider": "custom",
                     "base_url": "https://x/v1", "model": "x"}]
    })
    entry = find_model("m")
    entry.model = "mutated"
    assert find_model("m").model == "x"


def test_models_external_edit_invalidates(home):
    _write(home / "models.yaml", {
        "models": [{"name": "m", "provider": "custom",
                     "base_url": "https://x/v1", "model": "x"}]
    })
    assert find_model("m") is not None
    _write(home / "models.yaml", {
        "models": [{"name": "n", "provider": "custom",
                     "base_url": "https://x/v1", "model": "x"}]
    })
    assert find_model("m") is None
    assert find_model("n") is not None


def test_models_generation_bumps_on_reload_and_save(home):
    _write(home / "models.yaml", {"models": []})
    load_models_file()
    g0 = models_generation()
    _touch_rewrite(home / "models.yaml")
    load_models_file()
    assert models_generation() > g0


# ---------------------------------------------------------------------------
# Provider instance cache
# ---------------------------------------------------------------------------

def _entry(**kw) -> ModelEntry:
    base = dict(name="p", provider=ProviderType.CUSTOM,
                model="x", base_url="https://x/v1")
    base.update(kw)
    return ModelEntry(**base)


def test_provider_cache_reuses_same_instance(home):
    assert get_provider(_entry()) is get_provider(_entry())


def test_provider_cache_keyed_by_value(home):
    assert get_provider(_entry()) is not get_provider(_entry(model="other"))


def test_provider_cache_invalidated_by_models_save(home):
    e = _entry()
    p1 = get_provider(e)
    from modelbridge.config import load_models_file, save_models_file

    save_models_file(load_models_file())  # bumps models_generation
    p2 = get_provider(e)
    assert p2 is not p1


def test_provider_cache_env_key_change(home, monkeypatch):
    e = _entry(api_key_env="X_TEST_KEY")
    monkeypatch.setenv("X_TEST_KEY", "v1")
    p1 = get_provider(e)
    monkeypatch.setenv("X_TEST_KEY", "v2")
    p2 = get_provider(e)
    assert p1 is not p2
    assert p1.api_key == "v1" and p2.api_key == "v2"


# ---------------------------------------------------------------------------
# Cache stats write-through
# ---------------------------------------------------------------------------

def test_cache_stats_external_write_is_seen(home):
    from modelbridge.cache.manager import get_cache_path, load_cache_stats, record_hit

    record_hit(saved_tokens=5, model="m")
    assert load_cache_stats().hits == 1
    # Simulate another process writing the file.
    path = get_cache_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hits"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_cache_stats().hits == 99


def test_cache_stats_atomic_write_without_fsync(home):
    from modelbridge.cache.manager import get_cache_path, record_miss

    record_miss(model="m")
    assert get_cache_path().exists()
    json.loads(get_cache_path().read_text(encoding="utf-8"))  # valid JSON


# ---------------------------------------------------------------------------
# Pricing cache
# ---------------------------------------------------------------------------

def test_pricing_cache_sees_external_edit(home):
    from modelbridge.cost.estimator import load_pricing_overrides

    _write(home / "pricing.yaml", {"pricing": {"m": {
        "currency": "CNY", "input_per_1m": 1.0, "output_per_1m": 2.0}}})
    assert "m" in load_pricing_overrides()
    _write(home / "pricing.yaml", {"pricing": {"n": {
        "currency": "CNY", "input_per_1m": 1.0, "output_per_1m": 2.0}}})
    assert "m" not in load_pricing_overrides()
    assert "n" in load_pricing_overrides()


# ---------------------------------------------------------------------------
# httpx pool
# ---------------------------------------------------------------------------

def test_http_client_pool_reuses_by_origin():
    a = _shared_http_client("https://api.deepseek.com/v1/chat/completions")
    b = _shared_http_client("https://api.deepseek.com/other")
    c = _shared_http_client("https://api.moonshot.ai/v1")
    assert a is b
    assert a is not c


def test_http_client_pool_close():
    from modelbridge.providers.base import close_shared_http_clients

    client = _shared_http_client("https://pool-test.example.com")
    close_shared_http_clients()
    assert _shared_http_client("https://pool-test.example.com") is not client
