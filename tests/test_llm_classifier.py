"""Unit tests for the LLM-driven classifier.

``classify_task_llm`` asks the lowest-tier model to classify a prompt and
parses a strict-JSON verdict. These tests stub out the model call (no
network) via monkeypatch and pin the contract: tolerant JSON extraction,
caller-fact floors, and — critically — that EVERY failure mode raises
:class:`LLMClassifyError` instead of silently falling back to keywords.
"""

from __future__ import annotations

import types

import pytest

import modelbridge.router.llm_classifier as llm_mod
from modelbridge.models import ModelLevel
from modelbridge.providers import ProviderError
from modelbridge.router.fallback import FallbackResult
from modelbridge.router.llm_classifier import LLMClassifyError, classify_task_llm


def _stub_model(monkeypatch, *, content=None, model="tiny-x", raise_exc=None):
    """Point the classifier at a fake tiny model returning ``content``."""
    monkeypatch.setattr(
        llm_mod,
        "resolve_with_fallback",
        lambda level: FallbackResult(chosen_model=model, chosen_level=None, chain=[]),
    )

    def fake_chat_once(*_a, **_k):
        if raise_exc is not None:
            raise raise_exc
        return None, types.SimpleNamespace(content=content)

    monkeypatch.setattr(llm_mod, "chat_once", fake_chat_once)


VALID = (
    '{"task_type":"code_generate","complexity":"medium",'
    '"risk_level":"low","recommended_level":"coder","reason":"写函数"}'
)


def test_valid_json_parses(monkeypatch):
    _stub_model(monkeypatch, content=VALID)
    p = classify_task_llm("写个函数")
    assert p.task_type == "code_generate"
    assert p.complexity == "medium"
    assert p.recommended_level == ModelLevel.CODER
    assert p.risk_level == "low"


def test_json_in_code_fence_parses(monkeypatch):
    _stub_model(monkeypatch, content=f"```json\n{VALID}\n```")
    p = classify_task_llm("写个函数")
    assert p.recommended_level == ModelLevel.CODER


def test_json_with_surrounding_prose_parses(monkeypatch):
    _stub_model(monkeypatch, content=f"当然可以，结果如下：\n{VALID}\n希望有用")
    p = classify_task_llm("写个函数")
    assert p.task_type == "code_generate"


def test_empty_prompt_short_circuits_without_model(monkeypatch):
    # Must NOT need a model for an empty prompt.
    def boom(*_a, **_k):  # pragma: no cover - should never be called
        raise AssertionError("resolve_with_fallback should not be called")

    monkeypatch.setattr(llm_mod, "resolve_with_fallback", boom)
    p = classify_task_llm("   ")
    assert p.task_type == "unknown"
    assert p.recommended_level == ModelLevel.TINY


def test_no_tiny_model_raises(monkeypatch):
    monkeypatch.setattr(
        llm_mod,
        "resolve_with_fallback",
        lambda level: FallbackResult(chosen_model=None, chosen_level=None, chain=[]),
    )
    with pytest.raises(LLMClassifyError):
        classify_task_llm("写个函数")


def test_provider_error_raises_not_falls_back(monkeypatch):
    _stub_model(monkeypatch, raise_exc=ProviderError("timeout"))
    with pytest.raises(LLMClassifyError):
        classify_task_llm("写个函数")


def test_garbage_non_json_raises(monkeypatch):
    _stub_model(monkeypatch, content="我觉得是 coder 等级")
    with pytest.raises(LLMClassifyError):
        classify_task_llm("写个函数")


def test_invalid_enum_raises(monkeypatch):
    bad = (
        '{"task_type":"flying","complexity":"simple",'
        '"risk_level":"low","recommended_level":"cheap","reason":"x"}'
    )
    _stub_model(monkeypatch, content=bad)
    with pytest.raises(LLMClassifyError):
        classify_task_llm("写个函数")


def test_invalid_level_raises(monkeypatch):
    bad = (
        '{"task_type":"chat","complexity":"simple",'
        '"risk_level":"low","recommended_level":"super","reason":"x"}'
    )
    _stub_model(monkeypatch, content=bad)
    with pytest.raises(LLMClassifyError):
        classify_task_llm("写个函数")


def test_caller_fact_floor_wants_tools(monkeypatch):
    # LLM says cheap/explain, but wants_tools=True floors it to agent_task/agent.
    cheap = (
        '{"task_type":"explain","complexity":"simple",'
        '"risk_level":"low","recommended_level":"cheap","reason":"x"}'
    )
    _stub_model(monkeypatch, content=cheap)
    p = classify_task_llm("解释一下", wants_tools=True)
    assert p.recommended_level == ModelLevel.AGENT
    assert p.task_type == "agent_task"
