# tests/test_llm_classifier_robustness.py
"""LLM-classifier JSON extraction + context-token robustness."""

from __future__ import annotations

import pytest

from modelbridge.router.llm_classifier import LLMClassifyError, _extract_json


def test_extract_json_ignores_trailing_second_object():
    assert _extract_json('{"task_type":"chat"} {"x":1}') == {"task_type": "chat"}


def test_extract_json_with_prose_prefix():
    assert _extract_json('Sure! {"task_type":"chat"} done') == {"task_type": "chat"}


def test_extract_json_handles_nested_object():
    assert _extract_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_extract_json_raises_on_garbage():
    with pytest.raises(LLMClassifyError):
        _extract_json("no json at all")


def test_llm_classifier_honors_large_context_tokens(monkeypatch):
    from types import SimpleNamespace

    from modelbridge.models import ModelLevel
    from modelbridge.router import llm_classifier as llm_mod
    from modelbridge.router.llm_classifier import classify_task_llm

    monkeypatch.setattr(
        llm_mod, "resolve_with_fallback",
        lambda level: SimpleNamespace(chosen_model="tiny-x"),
    )

    def fake_chat_once(prompt, **kwargs):
        resp = SimpleNamespace(
            content='{"task_type":"chat","complexity":"simple",'
                    '"risk_level":"low","recommended_level":"cheap","reason":"t"}'
        )
        return (SimpleNamespace(), resp)

    monkeypatch.setattr(llm_mod, "chat_once", fake_chat_once)

    p = classify_task_llm("hi there", context_tokens=40000)
    assert p.recommended_level == ModelLevel.AGENT
