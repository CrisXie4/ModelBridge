"""ANTHROPIC_MESSAGES 传输层（E3）—— GLM Coding Plan /api/anthropic。

纯 wire 映射测试（无网络）：请求/响应/SSE/工具调用/cache usage 全部在
adapter 内映射到现有 ChatRequest/ChatResponse；``reasoning_content`` 不跨
传输回放（thinking 回放清理）。
"""

from __future__ import annotations

import json

import pytest

from modelbridge.models import ModelEntry, ProviderType, TransportType
from modelbridge.providers import AnthropicMessagesProvider, get_provider
from modelbridge.schemas import ChatMessage, ChatRequest, ProviderError


def _entry(**kw) -> ModelEntry:
    defaults = dict(
        name="glm-cp", provider=ProviderType.GLM, model="glm-5.3-flash",
        base_url="https://open.bigmodel.cn/api/anthropic",
        type=TransportType.ANTHROPIC_MESSAGES,
        capabilities={"tools": True},
    )
    defaults.update(kw)
    return ModelEntry(**defaults)


def _provider(**kw) -> AnthropicMessagesProvider:
    p = get_provider(_entry(**kw))
    assert isinstance(p, AnthropicMessagesProvider)
    return p


def test_transport_dispatch_by_type():
    """``type: anthropic_messages`` 优先按传输层分发，provider 只是逻辑提示。"""
    p = _provider()
    assert type(p).__name__ == "AnthropicMessagesProvider"
    assert p.chat_endpoint() == "https://open.bigmodel.cn/api/anthropic/v1/messages"
    # OpenAI-compat 路径不受影响。
    from modelbridge.providers import GLMProvider

    assert isinstance(
        get_provider(ModelEntry(
            name="g", provider=ProviderType.GLM, model="glm-5.2",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        )),
        GLMProvider,
    )


def _req(**kw) -> ChatRequest:
    defaults = dict(model="glm-5.3-flash", messages=[ChatMessage(role="user", content="hi")])
    defaults.update(kw)
    return ChatRequest(**defaults)


def test_build_payload_system_tools_and_thinking():
    p = _provider()
    req = _req(
        messages=[
            ChatMessage(role="system", content="SYS-A"),
            ChatMessage(role="system", content="SYS-B"),
            ChatMessage(role="user", content="问题"),
        ],
        tools=[{
            "type": "function",
            "function": {"name": "run_bash", "description": "跑命令",
                         "parameters": {"type": "object", "properties": {}}},
        }],
        tool_choice="auto",
        thinking=True, thinking_budget=4096,
    )
    body = p.build_chat_payload(req)
    assert body["system"] == "SYS-A\n\nSYS-B"
    assert body["max_tokens"] == 4096  # Anthropic 必填的缺省
    assert body["messages"] == [{"role": "user",
                                 "content": [{"type": "text", "text": "问题"}]}]
    assert body["tools"] == [{"name": "run_bash", "description": "跑命令",
                              "input_schema": {"type": "object", "properties": {}}}]
    assert body["tool_choice"] == {"type": "auto"}
    # thinking 硬约束②：budget 严格小于 max_tokens（4096 → 4095）。
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4095}
    assert "temperature" not in body  # thinking 启用时 temperature 被丢弃


def test_build_payload_tool_use_and_tool_result_roundtrip():
    """assistant tool_calls → tool_use 块；tool 结果合并进 user 的 tool_result。"""
    p = _provider()
    req = _req(messages=[
        ChatMessage(role="user", content="跑命令"),
        ChatMessage(role="assistant", content="", tool_calls=[
            {"id": "toolu_1", "type": "function",
             "function": {"name": "run_bash", "arguments": '{"command": "ls"}'}},
        ]),
        ChatMessage(role="tool", tool_call_id="toolu_1", content="file.txt"),
        ChatMessage(role="tool", tool_call_id="toolu_1", content="第二条结果"),
        ChatMessage(role="assistant", content="完成了"),
    ])
    body = p.build_chat_payload(req)
    roles = [(m["role"], [b["type"] for b in m["content"]]) for m in body["messages"]]
    assert roles == [
        ("user", ["text"]),
        ("assistant", ["tool_use"]),
        ("user", ["tool_result", "tool_result"]),  # 连续 tool 结果合并进一条 user
        ("assistant", ["text"]),
    ]
    tool_use = body["messages"][1]["content"][0]
    assert tool_use == {"type": "tool_use", "id": "toolu_1",
                        "name": "run_bash", "input": {"command": "ls"}}
    assert body["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"


def test_build_payload_drops_reasoning_content():
    """thinking 回放清理：reasoning_content 不跨传输回放（Anthropic 要求
    thinking 块原生完整），tool_calls 历史轮照常还原。"""
    p = _provider()
    req = _req(messages=[
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="答", reasoning_content="思考轨迹",
                    tool_calls=[
                        {"id": "t1", "type": "function",
                         "function": {"name": "f", "arguments": "{}"}},
                    ]),
    ])
    body = p.build_chat_payload(req)
    raw = json.dumps(body, ensure_ascii=False)
    assert "思考轨迹" not in raw
    assistant_blocks = body["messages"][1]["content"]
    assert [b["type"] for b in assistant_blocks] == ["text", "tool_use"]


def test_parse_response_maps_text_tool_use_usage():
    p = _provider()
    resp = p.parse_chat_response({
        "content": [
            {"type": "text", "text": "先看文件"},
            {"type": "tool_use", "id": "toolu_9", "name": "read_file",
             "input": {"path": "a.py"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3},
        "model": "glm-5.3-flash",
    })
    assert resp.content == "先看文件"
    assert resp.tool_calls == [{
        "id": "toolu_9", "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
    }]
    assert resp.finish_reason == "tool_calls"
    assert resp.usage == {
        "prompt_tokens": 10, "completion_tokens": 5,
        "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3,
    }
    # max_tokens / end_turn 的 stop_reason 映射
    assert p.parse_chat_response({"content": [], "stop_reason": "max_tokens"}).finish_reason == "length"
    assert p.parse_chat_response({"content": [], "stop_reason": "end_turn"}).finish_reason == "stop"


class _FakeStreamResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def read(self):
        return b""

    def iter_lines(self):
        yield from self._lines


class _FakeStreamClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kwargs):  # noqa: ARG002
        import contextlib

        @contextlib.contextmanager
        def cm():
            yield _FakeStreamResp(self._lines)

        return cm()


_SSE = [
    'data: {"type": "message_start", "message": {"usage": {"input_tokens": 42}}}',
    'data: {"type": "content_block_start", "index": 0, '
    '"content_block": {"type": "text"}}',
    'data: {"type": "content_block_delta", "index": 0, '
    '"delta": {"type": "text_delta", "text": "你好"}}',
    'data: {"type": "content_block_stop", "index": 0}',
    'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, '
    '"usage": {"output_tokens": 9}}',
    'data: {"type": "message_stop"}',
]


def test_stream_chat_maps_sse(monkeypatch):
    p = _provider()
    fake = _FakeStreamClient(_SSE)
    monkeypatch.setattr(
        "modelbridge.providers.base._shared_http_client", lambda url: fake
    )
    events = list(p.stream_chat(_req(), timeout=30))
    kinds = [e.kind for e in events]
    assert kinds[-1] == "done"
    assert any(e.kind == "content" and e.text == "你好" for e in events)
    final = events[-1].response
    assert final is not None
    assert final.content == "你好"
    assert final.finish_reason == "stop"
    assert final.usage == {"prompt_tokens": 42, "completion_tokens": 9}


def test_stream_chat_assembles_tool_use_from_json_deltas(monkeypatch):
    def _sse(obj) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False)

    lines = [
        _sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}),
        _sse({"type": "content_block_start", "index": 0,
              "content_block": {"type": "tool_use", "id": "toolu_2",
                                "name": "run_bash"}}),
        # 分两段到达的 tool input JSON 前缀（真实 wire 形态）。
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls"'}}),
        _sse({"type": "content_block_delta", "index": 0,
              "delta": {"type": "input_json_delta", "partial_json": "}"}}),
        _sse({"type": "content_block_stop", "index": 0}),
        _sse({"type": "message_delta", "delta": {"stop_reason": "tool_use"},
              "usage": {"output_tokens": 4}}),
        _sse({"type": "message_stop"}),
    ]
    p = _provider()
    monkeypatch.setattr(
        "modelbridge.providers.base._shared_http_client",
        lambda url: _FakeStreamClient(lines),
    )
    events = list(p.stream_chat(_req(), timeout=30))
    final = events[-1].response
    assert final is not None
    assert final.finish_reason == "tool_calls"
    assert final.tool_calls == [{
        "id": "toolu_2", "type": "function",
        "function": {"name": "run_bash", "arguments": '{"command": "ls"}'},
    }]


def test_stream_chat_error_event_raises(monkeypatch):
    lines = [
        'data: {"type": "error", "error": {"type": "overloaded_error", '
        '"message": "Overloaded"}}',
    ]
    p = _provider()
    monkeypatch.setattr(
        "modelbridge.providers.base._shared_http_client",
        lambda url: _FakeStreamClient(lines),
    )
    with pytest.raises(ProviderError) as ei:
        list(p.stream_chat(_req(), timeout=30))
    assert "Overloaded" in ei.value.message


# --- 评审修复：thinking 与 temperature/max_tokens 的硬约束协调 ---------------

def test_thinking_drops_temperature_and_clamps_budget():
    """评审场景：thinking 开 + budget=None + temperature=0.7 + 未配 max_tokens
    → temperature 丢弃、budget 钳到 max_tokens-1（不再 4096<8192 双重违规）。"""
    p = _provider()
    req = _req(temperature=0.7, thinking=True)  # budget 默认 8192 > max_tokens 4096
    body = p.build_chat_payload(req)
    assert "temperature" not in body
    assert body["max_tokens"] == 4096
    assert body["thinking"]["budget_tokens"] == 4095
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]


def test_thinking_raises_max_tokens_when_below_budget_floor():
    """max_tokens 小于 budget 下限（1024）→ 抬高 max_tokens = budget + 1。"""
    p = _provider()
    req = _req(max_tokens=1024, thinking=True, thinking_budget=8192)
    body = p.build_chat_payload(req)
    assert body["max_tokens"] == 1025
    assert body["thinking"]["budget_tokens"] == 1024
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]


def test_no_thinking_keeps_temperature():
    """未开 thinking 时 temperature 原样保留（修复不得误伤普通请求）。"""
    p = _provider()
    body = p.build_chat_payload(_req(temperature=0.7))
    assert body["temperature"] == 0.7
