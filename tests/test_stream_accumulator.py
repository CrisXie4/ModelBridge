# tests/test_stream_accumulator.py
"""Robustness of the OpenAI streaming-chunk accumulator."""

from __future__ import annotations

from modelbridge.providers.base import _StreamAccumulator


def _acc():
    return _StreamAccumulator(provider="t", model_default="m")


def test_consume_tolerates_non_numeric_index():
    acc = _acc()
    chunk = {"choices": [{"delta": {"tool_calls": [
        {"index": "bad", "id": "c1", "function": {"name": "f", "arguments": "{}"}}
    ]}}]}
    acc.consume(chunk)  # must not raise
    assert 0 in acc.tool_calls
    assert acc.tool_calls[0]["function"]["name"] == "f"


def test_consume_accumulates_split_tool_call_id():
    acc = _acc()
    acc.consume({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_", "function": {"name": "f"}}
    ]}}]})
    acc.consume({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "123"}
    ]}}]})
    assert acc.tool_calls[0]["id"] == "call_123"


def test_to_response_preserves_raw_chunks():
    acc = _acc()
    acc.consume({"choices": [{"delta": {"content": "hi"}}]})
    resp = acc.to_response()
    assert resp.raw["chunks"] == 1          # count preserved (back-compat)
    assert resp.raw["raw_chunks"] == acc.raw_chunks  # actual chunks retained
