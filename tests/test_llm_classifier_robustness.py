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
