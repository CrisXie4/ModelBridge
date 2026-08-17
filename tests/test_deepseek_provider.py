# tests/test_deepseek_provider.py
"""DeepSeek adapter wire-format rules ported from DeepSeek's official harness.

The rules verified here mirror ``packages/llm/llm-deepseek`` in
github.com/deepseek-ai/deepseek-harness:

* ``ChatRequest.thinking`` → top-level ``thinking: {type: enabled|disabled}``;
  budgets map to the coarse ``reasoning_effort`` (high/max, never "off").
* Assistant ``content`` is ``""`` — never ``null`` (API 400s otherwise).
* ``reasoning_content`` replayed only on tool-call turns (passback rule).
* Empty tool output crosses the wire as ``(no output)``.
"""

from __future__ import annotations

import pytest

from modelbridge.agent.thinking import MODEL_THINKING_PROFILES, profile_for
from modelbridge.cost.estimator import DEFAULT_PRICING
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers import get_provider
from modelbridge.providers.deepseek import DeepSeekProvider
from modelbridge.schemas import ChatMessage, ChatRequest


def _provider(**extra) -> DeepSeekProvider:
    entry = ModelEntry(
        name="ds",
        provider=ProviderType.DEEPSEEK,
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        **extra,
    )
    p = get_provider(entry)
    assert isinstance(p, DeepSeekProvider)
    return p


def _request(**kwargs) -> ChatRequest:
    defaults = dict(
        model="deepseek-v4-flash",
        messages=[ChatMessage(role="user", content="hi")],
    )
    defaults.update(kwargs)
    return ChatRequest(**defaults)


# ---------------------------------------------------------------------------
# Endpoint normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base,expected", [
    ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/chat/completions", "https://api.deepseek.com/chat/completions"),
])
def test_chat_endpoint_normalisation(base, expected):
    entry = ModelEntry(name="ds", provider=ProviderType.DEEPSEEK,
                       model="deepseek-v4-flash", base_url=base)
    assert DeepSeekProvider(entry).chat_endpoint() == expected


# ---------------------------------------------------------------------------
# Thinking translation
# ---------------------------------------------------------------------------

def test_thinking_off_serialises_disabled_and_drops_effort():
    p = _provider()
    body = p.build_chat_payload(_request(thinking=False))
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


def test_thinking_on_without_budget_sends_no_effort():
    p = _provider()
    body = p.build_chat_payload(_request(thinking=True))
    assert body["thinking"] == {"type": "enabled"}
    # No budget → let the server default apply (dsh default effort is high).
    assert "reasoning_effort" not in body


def test_budget_maps_to_effort_high_and_max():
    p = _provider()
    # v4-flash profile max is 8192: below → high, at/above → max.
    low = p.build_chat_payload(_request(thinking=True, thinking_budget=2048))
    assert low["reasoning_effort"] == "high"
    top = p.build_chat_payload(_request(thinking=True, thinking_budget=8192))
    assert top["reasoning_effort"] == "max"


def test_explicit_extra_body_beats_runtime_thinking_knobs():
    p = _provider()
    body = p.build_chat_payload(
        _request(thinking=False, extra_body={"thinking": {"type": "enabled"}})
    )
    assert body["thinking"] == {"type": "enabled"}


def test_no_thinking_signal_leaves_body_untouched():
    p = _provider()
    body = p.build_chat_payload(_request(thinking=None))
    assert "thinking" not in body
    assert "reasoning_effort" not in body


def test_static_effort_without_thinking_gets_paired_with_enabled():
    p = _provider(extra={"reasoning_effort": "max"})
    body = p.build_chat_payload(_request(thinking=None))
    assert body["reasoning_effort"] == "max"
    assert body["thinking"] == {"type": "enabled"}


def test_runtime_thinking_off_overrides_static_effort():
    p = _provider(extra={"reasoning_effort": "high"})
    body = p.build_chat_payload(_request(thinking=False))
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


# ---------------------------------------------------------------------------
# Message serialisation quirks
# ---------------------------------------------------------------------------

def test_assistant_null_content_becomes_empty_string():
    p = _provider()
    body = p.build_chat_payload(_request(
        messages=[
            ChatMessage(role="user", content="q"),
            # Reasoning-only turn: no content, no tool_calls. On the wire it
            # must carry content:"" or DeepSeek 400s ("content or tool_calls
            # must be set") and the session bricks on every later turn.
            ChatMessage(role="assistant", content=None, reasoning_content="想了想"),
            ChatMessage(role="user", content="go on"),
        ],
    ))
    wire = body["messages"][1]
    assert wire["content"] == ""
    # Passback rule: reasoning only returns on tool-call turns.
    assert "reasoning_content" not in wire


def test_reasoning_content_kept_on_tool_call_turns():
    p = _provider()
    body = p.build_chat_payload(_request(
        messages=[
            ChatMessage(
                role="assistant",
                content=None,
                reasoning_content="先查文件",
                tool_calls=[{"id": "c1", "type": "function",
                             "function": {"name": "read_file", "arguments": "{}"}}],
            ),
        ],
    ))
    wire = body["messages"][0]
    assert wire["content"] == ""
    assert wire["reasoning_content"] == "先查文件"
    assert wire["tool_calls"][0]["id"] == "c1"


def test_empty_tool_output_gets_placeholder():
    p = _provider()
    body = p.build_chat_payload(_request(
        messages=[ChatMessage(role="tool", tool_call_id="c1", content="")],
    ))
    assert body["messages"][0]["content"] == "(no output)"

    none_body = p.build_chat_payload(_request(
        messages=[ChatMessage(role="tool", tool_call_id="c1", content=None)],
    ))
    assert none_body["messages"][0]["content"] == "(no output)"


def test_plain_text_reasoning_turn_dropped_but_text_kept():
    p = _provider()
    body = p.build_chat_payload(_request(
        messages=[
            ChatMessage(role="assistant", content="答案", reasoning_content="思考"),
        ],
    ))
    wire = body["messages"][0]
    assert wire["content"] == "答案"
    assert "reasoning_content" not in wire


# ---------------------------------------------------------------------------
# Error hints
# ---------------------------------------------------------------------------

def test_400_hint_mentions_current_models_and_eol():
    p = _provider()
    err = p.normalize_error(status_code=400, body='{"error":{"message":"bad"}}')
    assert "deepseek-v4-flash" in (err.hint or "")
    assert "deepseek-v4-pro" in (err.hint or "")
    assert "2026-07-24" in (err.hint or "")


def test_quota_error_gets_recharge_hint():
    p = _provider()
    err = p.normalize_error(
        status_code=402,
        body='{"error":{"message":"Insufficient Balance"}}',
    )
    assert "充值" in (err.hint or "")


def test_context_overflow_error_gets_compact_hint():
    p = _provider()
    err = p.normalize_error(
        status_code=400,
        body='{"error":{"message":"This model maximum context length is 1000000 tokens"}}',
    )
    assert "/compact" in (err.hint or "")


# ---------------------------------------------------------------------------
# Catalog consistency
# ---------------------------------------------------------------------------

def test_v4_models_have_thinking_profiles():
    for mid in ("deepseek-v4", "deepseek-v4-pro", "deepseek-v4-flash"):
        assert profile_for(mid) is not None, mid
    assert "deepseek-v3.1" not in MODEL_THINKING_PROFILES


def test_v4_pricing_records_peak_rates_in_cny():
    for mid in ("deepseek-v4-pro", "deepseek-v4-flash"):
        pricing = DEFAULT_PRICING[mid]
        assert pricing.currency == "CNY"
        assert pricing.cache_hit_input_per_1m is not None
    # Official 2026-08-17 peak rates (¥/1M): flash 3/9, pro 9/27.
    assert DEFAULT_PRICING["deepseek-v4-flash"].input_per_1m == 3.0
    assert DEFAULT_PRICING["deepseek-v4-flash"].output_per_1m == 9.0
    assert DEFAULT_PRICING["deepseek-v4-pro"].input_per_1m == 9.0
    assert DEFAULT_PRICING["deepseek-v4-pro"].output_per_1m == 27.0
