# tests/test_stream_accumulator.py
"""Robustness of the OpenAI streaming-chunk accumulator."""

from __future__ import annotations

import pytest

from modelbridge.providers.base import _StreamAccumulator


def _acc():
    return _StreamAccumulator(provider="t", model_default="m")


@pytest.mark.parametrize("chunk", [
    {"choices": [None]},
    {"choices": ["oops"]},
    {"choices": [123]},
    {"choices": "bad"},
])
def test_consume_tolerates_non_dict_choice(chunk):
    # A malformed SSE chunk must not crash the stream with an AttributeError.
    acc = _acc()
    acc.consume(chunk)  # must not raise


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


# --- U10: HTTP 200 的 SSE 业务错误帧识别（success:false / code / 方括号文案）---

import pytest  # noqa: E402

from modelbridge.error_hints import is_retryable_error  # noqa: E402
from modelbridge.providers.base import detect_business_error  # noqa: E402
from modelbridge.schemas import ProviderError  # noqa: E402


def test_consume_surfaces_success_false_error_frame():
    """success:false 且无 choices 的帧 → kind=error 事件，不再静默吞掉。"""
    acc = _acc()
    events = acc.consume({
        "success": False, "msg": "余额不足",
        "error_code": 1316, "request_id": "req-9",
    })
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "error"
    err = ev.error
    assert isinstance(err, ProviderError)
    assert err.error_type == "business_quota_exhausted"
    assert "重试无效" in (err.hint or "")
    assert err.request_id == "req-9"
    assert "req-9" in (err.hint or "") and "报障必填" in (err.hint or "")
    assert not is_retryable_error(err)  # 额度终止型勿重试


def test_consume_bracketed_message_error_frame():
    """智谱系 "[1302][...]" 方括号文案（无结构化 code）→ 识别为限流可重试。"""
    acc = _acc()
    events = acc.consume({"error": {"message": "[1302][lizhu][req-abc]"}})
    assert events and events[0].kind == "error"
    err = events[0].error
    assert err is not None and err.error_type == "business_rate_limited"
    assert is_retryable_error(err)


def test_consume_1261_is_context_overflow():
    """1261 → 上下文超限提示（loop 的溢出恢复按该文案识别并压缩重试）。"""
    from modelbridge.agent.compact import is_context_overflow

    acc = _acc()
    events = acc.consume({"success": False, "error_code": "1261", "msg": "context limit"})
    err = events[0].error
    assert err is not None and err.error_type == "context_exceeded"
    assert "超过上下文" in f"{err.message} {err.hint}"
    assert is_context_overflow(err) is True
    assert not is_retryable_error(err)


def test_consume_normal_and_usage_chunks_untouched():
    """正常数据帧与 usage-only 帧绝不误判为业务错误（纯加法守护）。"""
    acc = _acc()
    assert acc.consume({"choices": [{"delta": {"content": "hi"}}]})[0].kind == "content"
    usage_events = acc.consume({"choices": [], "usage": {"total_tokens": 5}})
    assert all(ev.kind != "error" for ev in usage_events)
    assert acc.to_response().content == "hi"
    assert acc.to_response().usage == {"total_tokens": 5}


def test_detect_business_error_message_fallback_chain():
    """msg/error_code/code 与 error 子对象的兜底链；request_id 链。"""
    assert detect_business_error({"msg": "x", "error_code": 1005})["code"] == "1005"
    assert detect_business_error({"error": {"code": "3001", "msg": "y"}})["code"] == "3001"
    assert detect_business_error({"code": 0, "msg": "ok"}) is None  # 0 不算业务码
    rid = detect_business_error(
        {"success": False, "msg": "x", "error": {"request_id": "rid-2"}}
    )["request_id"]
    assert rid == "rid-2"


def test_stream_chat_raises_on_error_frame(tmp_path, monkeypatch):
    """stream_chat 收到业务错误帧 → 中止流并抛 ProviderError。"""
    from modelbridge.providers.base import HTTPProvider
    from modelbridge.providers import get_provider
    from modelbridge.models import ModelEntry, ProviderType

    entry = ModelEntry(
        name="t", provider=ProviderType.OPENAI_COMPATIBLE,
        model="m", base_url="https://x/v1",
    )
    p = get_provider(entry)
    assert isinstance(p, HTTPProvider)

    sse_body = (
        'data: {"error": {"message": "[1316][quota][rid-7]"}}\n\n'
        "data: [DONE]\n\n"
    )

    class _FakeStreamResp:
        status_code = 200
        def read(self):
            return b""
        def iter_lines(self):
            for line in sse_body.splitlines():
                yield line

    import contextlib as _cl

    @ _cl.contextmanager
    def _fake_stream_cm(*a, **k):
        yield _FakeStreamResp()

    class _FakeClient:
        def stream(self, *a, **k):
            return _fake_stream_cm()

    monkeypatch.setattr(
        "modelbridge.providers.base._shared_http_client", lambda url: _FakeClient()
    )
    from modelbridge.schemas import ChatMessage, ChatRequest

    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(ProviderError) as ei:
        list(p.stream_chat(req, timeout=5))
    assert ei.value.error_type == "business_quota_exhausted"
    assert ei.value.request_id is None  # 方括号文案只带 code，request_id 走字段链
    assert is_retryable_error(ei.value) is False


def test_normal_frame_with_http_style_code_is_not_business_error():
    """评审回归：带正常 choices 且 success 非 false 的帧（即使含非零 code，
    如网关放的 HTTP 风格 code:200）一律按正常数据处理，不误判为业务错误。"""
    frame = {
        "code": 200, "msg": "success",
        "choices": [{"delta": {"content": "hi"}}], "id": "x",
    }
    assert detect_business_error(frame) is None
    acc = _acc()
    events = acc.consume(frame)
    assert [e.kind for e in events] == ["content"]
    assert acc.to_response().content == "hi"
    assert acc.to_response().finish_reason is None


def test_parse_chat_response_accepts_body_with_code_200():
    """parse_chat_response 对带 code:200 的正常 200 响应体正常出 ChatResponse，
    不再抛“业务码 200”。"""
    from modelbridge.models import ModelEntry, ProviderType
    from modelbridge.providers import get_provider

    p = get_provider(ModelEntry(
        name="t", provider=ProviderType.OPENAI_COMPATIBLE,
        model="m", base_url="https://x/v1",
    ))
    resp = p.parse_chat_response({
        "code": 200, "msg": "success",
        "choices": [{
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }],
    })
    assert resp.content == "ok"
    assert resp.finish_reason == "stop"
