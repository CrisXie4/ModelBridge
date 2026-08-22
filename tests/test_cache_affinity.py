# tests/test_cache_affinity.py
"""Cache-affinity behaviour: key derivation, opt-in wire injection, and the
per-model telemetry that makes model-switch cache costs visible.

Doctrine under test (from DeepSeek's official harness, cross-checked
2026-08): every provider+model pair is its own prefix-cache domain, and
DeepSeek's wire format has NO body-level cache key. So:

* ``ChatRequest.cache_key`` only reaches the wire when the model declares
  ``extra.cache_key_field`` (e.g. OpenAI-style ``prompt_cache_key``);
* DeepSeek never gets one injected;
* hit/miss telemetry is attributed per model so switches are observable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelbridge.cache.affinity import (
    cache_switch_note,
    derive_cache_key,
    sanitize_cache_key,
    session_cache_key,
)
from modelbridge.cache.manager import load_cache_stats, record_hit, record_miss
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers import get_provider
from modelbridge.providers.deepseek import DeepSeekProvider
from modelbridge.schemas import ChatMessage, ChatRequest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolate ~/.modelbridge so config/cache reads never touch the real home."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def test_derive_cache_key_is_deterministic_and_model_agnostic():
    a = derive_cache_key("abcd1234")
    b = derive_cache_key("abcd1234")
    assert a == b
    assert a.startswith("mb-")
    # Same prefix hash → same key regardless of model / session: switching
    # models mid-conversation keeps the key stable.
    assert len(a) <= 64


def test_derive_cache_key_changes_with_prefix():
    assert derive_cache_key("abcd1234") != derive_cache_key("ffff0000")


def test_sanitize_cache_key_forces_wire_charset():
    assert sanitize_cache_key("a b/c:dé") == "a_b_c_d_"
    assert len(sanitize_cache_key("x" * 200)) == 64


def test_empty_prefix_hash_still_yields_key():
    # PromptBuilder hashes empty prefixes to "00000000" — still a valid key.
    assert derive_cache_key("00000000").startswith("mb-")


# ---------------------------------------------------------------------------
# Wire injection (opt-in via extra.cache_key_field)
# ---------------------------------------------------------------------------

def _custom_entry(**extra) -> ModelEntry:
    return ModelEntry(
        name="custom",
        provider=ProviderType.CUSTOM,
        model="some-model",
        base_url="https://gw.example.com/v1",
        **extra,
    )


def _request(**kwargs) -> ChatRequest:
    defaults = dict(
        model="some-model",
        messages=[ChatMessage(role="user", content="hi")],
    )
    defaults.update(kwargs)
    return ChatRequest(**defaults)


def test_injected_when_model_declares_cache_key_field():
    entry = _custom_entry(extra={"cache_key_field": "prompt_cache_key"})
    body = get_provider(entry).build_chat_payload(
        _request(cache_key="mb-abc123")
    )
    assert body["prompt_cache_key"] == "mb-abc123"


def test_not_injected_without_cache_key_field():
    entry = _custom_entry()
    body = get_provider(entry).build_chat_payload(
        _request(cache_key="mb-abc123")
    )
    assert "prompt_cache_key" not in body


def test_not_injected_when_request_has_no_key():
    entry = _custom_entry(extra={"cache_key_field": "prompt_cache_key"})
    body = get_provider(entry).build_chat_payload(_request())
    assert "prompt_cache_key" not in body


def test_explicit_extra_body_wins_over_injection():
    entry = _custom_entry(extra={"cache_key_field": "prompt_cache_key"})
    body = get_provider(entry).build_chat_payload(
        _request(cache_key="mb-auto", extra_body={"prompt_cache_key": "mb-manual"})
    )
    assert body["prompt_cache_key"] == "mb-manual"


def test_injected_key_is_sanitized():
    entry = _custom_entry(extra={"cache_key_field": "prompt_cache_key"})
    body = get_provider(entry).build_chat_payload(
        _request(cache_key="bad key with spaces!!" + "x" * 100)
    )
    assert body["prompt_cache_key"] == sanitize_cache_key("bad key with spaces!!" + "x" * 100)


def test_deepseek_never_gets_a_body_cache_key():
    # DeepSeek's wire format defines no body cache key (dsh llm-deepseek
    # WireRequest) — and cache domains are per provider+model anyway.
    entry = ModelEntry(
        name="ds",
        provider=ProviderType.DEEPSEEK,
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )
    p = get_provider(entry)
    assert isinstance(p, DeepSeekProvider)
    body = p.build_chat_payload(
        _request(model="deepseek-v4-flash", cache_key="mb-whatever")
    )
    assert "prompt_cache_key" not in body
    assert "user_id" not in body


# ---------------------------------------------------------------------------
# session_cache_key
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self, metadata):
        self.metadata = metadata


def test_session_cache_key_reads_metadata(home):
    s = _FakeSession({"cache_key": "mb-deadbeef"})
    assert session_cache_key(s) == "mb-deadbeef"


def test_session_cache_key_none_without_metadata(home):
    assert session_cache_key(_FakeSession({})) is None


def test_session_cache_key_disabled_via_config(home):
    (home / "config.yaml").write_text(
        "cache:\n  enabled: false\n", encoding="utf-8"
    )
    assert session_cache_key(_FakeSession({"cache_key": "mb-x"})) is None


def test_session_cache_key_disabled_via_affinity_flag(home):
    (home / "config.yaml").write_text(
        "cache:\n  affinity_key: false\n", encoding="utf-8"
    )
    assert session_cache_key(_FakeSession({"cache_key": "mb-x"})) is None


# ---------------------------------------------------------------------------
# Agent-loop wiring
# ---------------------------------------------------------------------------

def test_run_agent_turn_passes_session_cache_key(monkeypatch, home):
    import modelbridge.agent.loop as loop

    captured: dict = {}

    class _Provider:
        def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
            captured["cache_key"] = request.cache_key
            from modelbridge.schemas import ChatResponse

            return ChatResponse(content="ok")

    monkeypatch.setattr(loop, "get_provider", lambda e: _Provider())
    monkeypatch.setattr(
        loop,
        "get_model_entry",
        lambda n: _custom_entry(),
    )

    from modelbridge.agent.context import AgentContext, auto_yes
    from modelbridge.agent.security import PathPolicy
    from modelbridge.agent.session import Session
    from modelbridge.agent.tools import ToolRegistry

    session = Session(model_name="custom")
    session.metadata = {"cache_key": "mb-loopkey"}
    session.add_user("go")

    loop.run_agent_turn(
        session=session,
        ctx=AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes),
        registry=ToolRegistry(),
        model_name="custom",
    )
    assert captured["cache_key"] == "mb-loopkey"


# ---------------------------------------------------------------------------
# Per-model telemetry
# ---------------------------------------------------------------------------

def test_per_model_stats_accumulate(home):
    record_hit(saved_tokens=100, saved_cost=0.01, model="ds-flash")
    record_hit(saved_tokens=50, saved_cost=0.005, model="ds-flash")
    record_miss(model="kimi-k3")

    s = load_cache_stats()
    assert s.per_model["ds-flash"]["hits"] == 2
    assert s.per_model["ds-flash"]["saved_tokens"] == 150
    assert s.per_model["kimi-k3"]["misses"] == 1
    assert s.hits == 2 and s.misses == 1


def test_per_model_omitted_keeps_legacy_file_loadable(home):
    # cache.json written by an older ModelBridge has no per_model block.
    legacy = {
        "strategy": "stable-prefix",
        "enabled": True,
        "hits": 7,
        "misses": 3,
        "saved_tokens": 1000,
        "estimated_savings": 0.5,
        "currency": "CNY",
    }
    from modelbridge.cache.manager import get_cache_path, save_cache_stats
    from modelbridge.cache import CacheStats

    get_cache_path().write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    s = load_cache_stats()
    assert s.hits == 7
    assert s.per_model == {}
    # Round-trip keeps the (empty) block for forward compatibility.
    save_cache_stats(s)
    assert "per_model" in json.loads(get_cache_path().read_text(encoding="utf-8"))
    assert CacheStats.from_dict(legacy).per_model == {}


def test_cache_hit_rate_savings_use_real_pricing(home):
    """_record_cache_outcome prefers cache_hit_input_per_1m over the 0.75 heuristic."""
    from modelbridge.cli import _record_cache_outcome
    from modelbridge.schemas import ChatResponse

    entry = ModelEntry(
        name="ds-test",
        provider=ProviderType.DEEPSEEK,
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )
    resp = ChatResponse(
        content="ok",
        usage={"prompt_cache_hit_tokens": 1_000_000, "prompt_cache_miss_tokens": 10},
    )
    _record_cache_outcome(entry, resp)
    s = load_cache_stats()
    # flash peak: input ¥3 vs cache-hit ¥0.10 per 1M → ¥2.90 saved per 1M hit.
    assert s.per_model["ds-test"]["hits"] == 1
    assert s.saved_tokens == 1_000_000
    assert s.estimated_savings == pytest.approx(2.90)
    # The uncached 10 tokens bill at the FULL input rate — they cost money too.
    assert s.billed_tokens == 1_000_010
    assert s.spend == pytest.approx(1_000_000 / 1e6 * 0.10 + 10 / 1e6 * 3.0)


def test_cache_miss_turn_prices_prompt_tokens(home):
    """A miss-only turn records the miss tokens + their full-rate cost.

    Regression: miss turns used to bump a counter but record no tokens and
    no spend, so the billed prompt never entered the stats.
    """
    from modelbridge.cli import _record_cache_outcome
    from modelbridge.schemas import ChatResponse

    entry = ModelEntry(
        name="ds-miss",
        provider=ProviderType.DEEPSEEK,
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )
    resp = ChatResponse(
        content="ok",
        usage={"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 500_000},
    )
    _record_cache_outcome(entry, resp)
    s = load_cache_stats()
    assert s.misses == 1
    assert s.per_model["ds-miss"]["misses"] == 1
    assert s.billed_tokens == 500_000
    assert s.spend == pytest.approx(500_000 / 1e6 * 3.0)


def test_cache_no_report_falls_back_to_local_prompt_tokens(home):
    """Provider reports no cache fields (and maybe no usage at all) — the
    locally-built prompt still gets billed, so its estimated tokens count
    as a miss with full-rate spend instead of being skipped."""
    from modelbridge.cli import _record_cache_outcome
    from modelbridge.schemas import ChatMessage, ChatResponse

    entry = ModelEntry(
        name="q-test",
        provider=ProviderType.CUSTOM,
        model="qwen3.7-plus",
        base_url="https://gw.example.com/v1",
    )
    msgs = [
        ChatMessage(role="system", content="你是助手" * 200),
        ChatMessage(role="user", content="帮我总结这段很长的文本" * 100),
    ]

    # Case 1: usage present but without any cache breakdown.
    resp = ChatResponse(content="ok", usage={"prompt_tokens": 7_000,
                                             "completion_tokens": 300})
    _record_cache_outcome(entry, resp, prompt_messages=msgs)
    s = load_cache_stats()
    assert s.misses == 1
    assert s.billed_tokens == 7_000
    assert s.spend == pytest.approx(7_000 / 1e6 * 2.0)  # qwen3.7-plus ¥2/1M

    # Case 2: no usage at all → local estimate of the sent messages.
    resp2 = ChatResponse(content="ok")
    _record_cache_outcome(entry, resp2, prompt_messages=msgs)
    s = load_cache_stats()
    assert s.misses == 2
    assert s.billed_tokens > 7_000  # local estimate added on top
    from modelbridge.context import estimate_session_tokens

    assert s.billed_tokens == 7_000 + estimate_session_tokens(msgs)
    assert s.per_model["q-test"]["spend"] == pytest.approx(s.spend)

    # Case 3: nothing reported and no messages to estimate → still a no-op.
    resp3 = ChatResponse(content="ok")
    _record_cache_outcome(entry, resp3)
    assert load_cache_stats().misses == 2


def test_switch_note_distinguishes_domains():
    ds_flash = ModelEntry(
        name="flash", provider=ProviderType.DEEPSEEK,
        model="deepseek-v4-flash", base_url="https://api.deepseek.com",
    )
    ds_pro = ModelEntry(
        name="pro", provider=ProviderType.DEEPSEEK,
        model="deepseek-v4-pro", base_url="https://api.deepseek.com",
    )
    kimi = ModelEntry(
        name="kimi", provider=ProviderType.KIMI,
        model="kimi-k3", base_url="https://api.moonshot.ai/v1",
    )
    # Same vendor, different model: still a fresh cache domain.
    note = cache_switch_note(ds_flash, ds_pro)
    assert note and "同厂商" in note
    # Different vendor.
    assert "切换厂商" in cache_switch_note(ds_flash, kimi)
    # Same model: nothing to say.
    assert cache_switch_note(ds_flash, ds_flash) == ""
    # Missing entries: nothing to say.
    assert cache_switch_note(None, ds_pro) == ""


# ---------------------------------------------------------------------------
# --route path shares the builder prefix (no more minimal-message bypass)
# ---------------------------------------------------------------------------

def test_chat_with_routing_uses_builder_messages(monkeypatch, home, capsys):
    """The routed path must send PromptBuilder messages + the derived
    cache key, and record the provider-reported cache outcome per model."""
    import types

    import modelbridge.cli as cli
    from modelbridge.cache.manager import load_cache_stats
    from modelbridge.prompt import PromptBuilder

    (home / "models.yaml").write_text(
        "models:\n"
        "  - name: m1\n"
        "    provider: custom\n"
        "    base_url: https://gw.example.com/v1\n"
        "    model: some-model\n",
        encoding="utf-8",
    )
    (home / "config.yaml").write_text(
        "default_model: m1\n", encoding="utf-8"
    )

    captured: dict = {}

    class _Provider:
        def chat(self, request, *, timeout, save_raw=False, verbose_label=""):  # noqa: ARG002
            captured["request"] = request
            from modelbridge.schemas import ChatResponse

            return ChatResponse(
                content="routed!",
                usage={"prompt_cache_hit_tokens": 500, "prompt_cache_miss_tokens": 5},
            )

    monkeypatch.setattr(
        cli,
        "route_prompt",
        lambda *a, **k: types.SimpleNamespace(chosen_model="m1", chosen_level=None),
    )
    monkeypatch.setattr(cli, "_print_route_result", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_provider", lambda e: _Provider())

    result = PromptBuilder().with_system_prompt("S").with_user_request("q").build()
    cli._chat_with_routing(
        "q",
        result,
        timeout=5.0,
        thinking=None,
        thinking_budget=None,
        verbose=False,
        mode=None,
        fallback=False,
    )

    req = captured["request"]
    # Same messages the direct path would send — the stable prefix is not
    # bypassed under --route anymore.
    assert req.messages == result.messages
    assert req.cache_key == derive_cache_key(result.stable_prefix_hash)
    # Provider-reported hit attributed to the routed model.
    s = load_cache_stats()
    assert s.per_model["m1"]["hits"] == 1
    assert s.per_model["m1"]["saved_tokens"] == 500
