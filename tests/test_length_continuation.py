"""finish_reason=length 自动续写（agent loop 的截断恢复）。

契约（见 :func:`modelbridge.agent.loop.run_agent_turn` 的
"Truncated-answer continuation" 一节）：

* 无 tool_calls 且 ``finish_reason == "length"`` → 追加一条"从截断处继续"
  的 user 消息并再请求一轮（截断的 assistant 已在历史里，追加 user 保持
  user/assistant 交替合法）。
* 上限 ``_MAX_LENGTH_CONTINUATIONS``（3）次；仍截断则按 stop 收尾并通知。
* 配置 ``prompt.auto_continue_on_length: false`` 关闭。
* 有 tool_calls 时（即使 finish_reason=length）走工具路径，不受影响。

网络无关：provider 被替换为按脚本逐个返回 ``ChatResponse`` 的 fake。
"""

from __future__ import annotations

from pathlib import Path

import modelbridge.agent.loop as loop
import modelbridge.config as config_mod
from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.loop import (
    _LENGTH_CONTINUE_PROMPT,
    _MAX_LENGTH_CONTINUATIONS,
    run_agent_turn,
)
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.session import Session
from modelbridge.agent.tools import ToolRegistry
from modelbridge.agent.tools.base import Tool, ToolResult
from modelbridge.models import AppConfig, ModelEntry, PromptConfig, ProviderType
from modelbridge.schemas import ChatResponse


class _ScriptedProvider:
    """按脚本逐个返回 ChatResponse，并记录每次请求的消息快照。"""

    def __init__(self, responses: list[ChatResponse]):
        self.responses = list(responses)
        self.calls = 0
        self.requests: list[list] = []

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        self.calls += 1
        self.requests.append(list(request.messages))
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _resp(content: str, finish_reason: str | None = "stop") -> ChatResponse:
    return ChatResponse(content=content, finish_reason=finish_reason)


def _setup(monkeypatch, provider, *, prompt_cfg: PromptConfig | None = None):
    monkeypatch.setattr(loop, "get_provider", lambda e: provider)
    monkeypatch.setattr(
        loop,
        "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE,
            model="f", base_url="http://x/v1",
        ),
    )
    # system_context_updates 关闭：它会在 step 边界注入 <system-update> user
    # 消息，与续写断言无关（默认 _setup 只关心续写路径本身）。
    base_cfg = PromptConfig(system_context_updates=False)
    monkeypatch.setattr(
        config_mod,
        "load_app_config",
        lambda: AppConfig(prompt=prompt_cfg or base_cfg),
    )
    ctx = AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes)
    session = Session(model_name="fake")
    session.add_user("写一篇长文")
    notices: list[str] = []
    return session, ctx, ToolRegistry(), notices


def test_truncated_answer_continues_and_finishes(monkeypatch):
    provider = _ScriptedProvider([_resp("前半", "length"), _resp("前半后半", "stop")])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert result.stopped_reason == "stop"
    assert result.final_response is not None
    assert result.final_response.content == "前半后半"
    assert provider.calls == 2
    assert result.iterations == 2
    # 历史：user → assistant(截断) → user(续写指令) → assistant(完整)
    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert session.messages[2].content == _LENGTH_CONTINUE_PROMPT
    # 续写指令确实进入了第二次请求。
    assert any(
        m.content == _LENGTH_CONTINUE_PROMPT for m in provider.requests[1]
    )
    assert any("自动续写 (1/" in n for n in notices)


def test_continuation_exhausts_after_three(monkeypatch):
    provider = _ScriptedProvider([_resp(f"part{i}", "length") for i in range(4)])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    # 1 次原始 + 3 次续写 = 4 次请求；不再第 5 次。
    assert provider.calls == 1 + _MAX_LENGTH_CONTINUATIONS
    assert result.stopped_reason == "stop"
    continues = [
        m for m in session.messages
        if m.role == "user" and m.content == _LENGTH_CONTINUE_PROMPT
    ]
    assert len(continues) == _MAX_LENGTH_CONTINUATIONS
    assert any("上限" in n and "不再重试" in n for n in notices)
    assert sum("自动续写 (" in n for n in notices) == _MAX_LENGTH_CONTINUATIONS


def test_config_disables_continuation(monkeypatch):
    provider = _ScriptedProvider([_resp("截断的回答", "length")])
    cfg = PromptConfig(auto_continue_on_length=False, system_context_updates=False)
    session, ctx, registry, notices = _setup(monkeypatch, provider, prompt_cfg=cfg)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 1
    assert result.final_response.content == "截断的回答"
    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant"]  # 未追加续写指令
    assert notices == []


def test_normal_stop_is_untouched(monkeypatch):
    provider = _ScriptedProvider([_resp("正常回答", "stop")])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 1
    assert result.stopped_reason == "stop"
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert notices == []


def test_tool_calls_with_length_finish_reason_takes_tool_path(monkeypatch):
    """带 tool_calls 的截断响应走工具路径，不注入续写指令。"""
    tool_calls = [{
        "id": "call_1", "type": "function",
        "function": {"name": "echo", "arguments": '{"text": "hi"}'},
    }]
    provider = _ScriptedProvider([
        ChatResponse(content="", tool_calls=tool_calls, finish_reason="length"),
        _resp("工具之后的回答", "stop"),
    ])
    session, ctx, registry, notices = _setup(monkeypatch, provider)
    registry.register(_EchoTool())

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 2
    assert result.final_response.content == "工具之后的回答"
    assert [m.role for m in session.messages] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert not any(
        m.content == _LENGTH_CONTINUE_PROMPT for m in session.messages
    )
    assert notices == []


class _EchoTool(Tool):
    name = "echo"
    description = "回声测试工具"

    def json_schema(self):
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    def execute(self, args, ctx):  # noqa: ARG002
        return ToolResult(content=f"echo: {args.get('text', '')}")


# --- E1②: 空补全单次重试 -----------------------------------------------------

def test_empty_completion_retried_once(monkeypatch):
    """首响应完全为空（无 content/reasoning/tool_calls/usage）→ 重发一次。"""
    provider = _ScriptedProvider([_resp("", "stop"), _resp("真正的回答", "stop")])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 2
    assert result.final_response.content == "真正的回答"
    assert any("空补全" in n for n in notices)
    # 空响应不入历史：user → assistant(真实回答)，无中间空 assistant。
    assert [m.role for m in session.messages] == ["user", "assistant"]


def test_empty_completion_retry_is_bounded(monkeypatch):
    provider = _ScriptedProvider([_resp("", "stop"), _resp("", "stop")])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 2  # 1 次原始 + 1 次重试，止步
    assert result.stopped_reason == "stop"
    assert sum("空补全" in n for n in notices) == 1


def test_response_with_usage_is_not_empty(monkeypatch):
    """有 usage（无内容）不算空补全 —— 不重试，照常入历史。"""
    provider = _ScriptedProvider([
        ChatResponse(content="", finish_reason="stop", usage={"total_tokens": 3}),
    ])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 1
    assert notices == []
    assert result.final_response.usage == {"total_tokens": 3}


def test_empty_completion_with_length_goes_to_continuation(monkeypatch):
    """空响应 + finish_reason=length 归 U4 续写管，不走空补全重试。"""
    provider = _ScriptedProvider([
        _resp("", "length"),
        _resp("续写出的回答", "stop"),
    ])
    session, ctx, registry, notices = _setup(monkeypatch, provider)

    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )

    assert provider.calls == 2
    assert result.final_response.content == "续写出的回答"
    assert any("续写" in n for n in notices)
    assert not any("空补全" in n for n in notices)


class _StreamScriptedProvider:
    """stream_chat 按脚本逐次产出事件序列（E1② 流式路径）。"""

    def __init__(self, scripts: list[list[tuple]]):
        self.scripts = scripts
        self.calls = 0

    def stream_chat(self, request, *, timeout):  # noqa: ARG002
        from modelbridge.providers.base import StreamEvent

        script = self.scripts[min(self.calls, len(self.scripts) - 1)]
        self.calls += 1
        yield from [StreamEvent(kind=k, text=t, **kw) for k, t, kw in script]

    def chat(self, request, **kw):  # noqa: ARG002 — 流式路径不应走非流接口
        raise AssertionError("stream path must not fall back to chat()")


def test_stream_empty_completion_retried_once(monkeypatch):
    """流式：首次流出空 done（无任何增量）→ 重试一次拿到真实内容。"""
    empty_done = ChatResponse(content="", finish_reason="stop")
    good_done = ChatResponse(content="流式回答", finish_reason="stop")
    provider = _StreamScriptedProvider([
        [("done", "", {"response": empty_done})],
        [("content", "流式回答", {}), ("done", "", {"response": good_done})],
    ])
    monkeypatch.setattr(loop, "get_provider", lambda e: provider)
    monkeypatch.setattr(
        loop, "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE,
            model="f", base_url="http://x/v1",
        ),
    )
    monkeypatch.setattr(
        config_mod, "load_app_config",
        lambda: AppConfig(prompt=PromptConfig(system_context_updates=False)),
    )
    from modelbridge.agent.context import AgentContext, auto_yes as _yes
    from modelbridge.agent.security import PathPolicy as _PP

    ctx = AgentContext(policy=_PP([], []), cwd=Path.cwd(), approve=_yes)
    session = Session(model_name="fake")
    session.add_user("hi")
    deltas: list[str] = []

    result = run_agent_turn(
        session=session, ctx=ctx, registry=ToolRegistry(), model_name="fake",
        stream=True, on_content_delta=deltas.append,
    )

    assert provider.calls == 2
    assert result.final_response.content == "流式回答"
    assert deltas == ["流式回答"]  # 首次空尝试没有向 UI 吐出任何增量
