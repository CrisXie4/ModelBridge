"""Session memory — assistant messages keep only the raw *delta*.

Providers echo the full assistant message object back in ``raw_message``;
storing it verbatim duplicates content/tool_calls/reasoning_content for the
whole session lifetime. The only consumer (MiMo adapter) merges keys missing
from ``to_wire()``, so a delta preserves behaviour exactly at a fraction of
the memory.
"""

from __future__ import annotations

import json as _json
from io import StringIO

from rich.console import Console

from modelbridge.agent.commands import SlashContext, handle_slash
from modelbridge.agent.session import Session, load_session
from modelbridge.schemas import ChatMessage, ChatResponse


def _resp(**kw) -> ChatResponse:
    defaults = dict(content="答案", provider="p")
    defaults.update(kw)
    return ChatResponse(**defaults)


def test_add_assistant_stores_no_redundant_raw():
    s = Session(model_name="m")
    s.add_assistant(_resp(
        content="hello",
        tool_calls=[{"id": "1", "type": "function", "function": {"name": "f"}}],
        reasoning_content="思考",
        raw_message={
            "role": "assistant",
            "content": "hello",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f"}}],
            "reasoning_content": "思考",
        },
    ))
    msg = s.messages[-1]
    assert msg.raw is None  # every key already covered by to_wire()
    assert msg.content == "hello"
    assert msg.tool_calls is not None
    assert msg.reasoning_content == "思考"


def test_add_assistant_keeps_wire_extra_keys_only():
    s = Session(model_name="m")
    s.add_assistant(_resp(
        content="x",
        raw_message={"role": "assistant", "content": "x", "mimo_extra": "keep-me"},
    ))
    msg = s.messages[-1]
    assert msg.raw == {"mimo_extra": "keep-me"}


def test_add_assistant_non_dict_raw_is_dropped():
    s = Session(model_name="m")
    # raw_message defaults to {} — no dict content, nothing worth keeping.
    s.add_assistant(_resp(content="x"))
    assert s.messages[-1].raw is None


def test_mimo_merge_still_sees_extra_keys():
    """The MiMo adapter merges raw keys to_wire() misses — delta suffices."""
    from modelbridge.providers.mimo import MiMoProvider

    m = ChatMessage(
        role="assistant", content="c", reasoning_content="r",
        raw={"mimo_extra": "keep-me"},
    )
    wire = MiMoProvider._serialize_message(MiMoProvider.__new__(MiMoProvider), m)
    assert wire["mimo_extra"] == "keep-me"
    assert wire["reasoning_content"] == "r"


# --- U12: 会话恢复（Session.from_dict / load_session / /resume） --------------


def _session_with_history() -> Session:
    s = Session(model_name="deepseek-chat", metadata={"cwd": "/x", "custom": 1})
    s.add_system("SYSTEM PREFIX")
    s.add_user("问题")
    s.add_assistant(_resp(
        content="回答",
        tool_calls=[{"id": "c1", "type": "function",
                     "function": {"name": "f", "arguments": "{}"}}],
        reasoning_content="思考中",
    ))
    s.add_tool_result(tool_call_id="c1", content="工具结果")
    return s


def test_from_dict_roundtrip_lossless():
    """from_dict 是 to_dict 的逆：messages/metadata/model 往返无损。"""
    s = _session_with_history()
    restored = Session.from_dict(s.to_dict())
    assert restored.model_name == s.model_name
    assert [m.role for m in restored.messages] == ["system", "user", "assistant", "tool"]
    a = restored.messages[2]
    assert a.content == "回答"
    assert a.tool_calls is not None and a.tool_calls[0]["id"] == "c1"
    assert a.reasoning_content == "思考中"
    assert restored.metadata == {"cwd": "/x", "custom": 1}


def test_load_session_file_roundtrip(tmp_path):
    p = tmp_path / "session.json"
    s = _session_with_history()
    p.write_text(_json.dumps(s.to_dict(), ensure_ascii=False), encoding="utf-8")
    loaded = load_session(p)
    assert loaded is not None
    assert [m.role for m in loaded.messages] == ["system", "user", "assistant", "tool"]
    assert loaded.messages[2].reasoning_content == "思考中"


def test_load_session_missing_or_corrupt_degrades_to_none(tmp_path):
    assert load_session(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_session(bad) is None
    wrong = tmp_path / "wrong.json"
    wrong.write_text('["not", "a", "dict"]', encoding="utf-8")
    assert load_session(wrong) is None


def test_from_dict_skips_broken_entries_and_defaults():
    data = {
        "messages": [
            {"role": "user", "content": "ok"},
            {"role": 123, "content": "x"},  # role 非法 —— 校验失败则跳过
            "not-a-dict",
        ],
    }
    s = Session.from_dict(data)
    assert s.model_name == "resumed"  # 缺 model 走默认
    assert [m.role for m in s.messages] == ["user"]


def test_slash_resume_replaces_history_in_place(tmp_path):
    """REPL /resume：就地替换 messages/metadata，保留 Session 对象引用。"""
    saved = _session_with_history()
    p = tmp_path / "s.json"
    p.write_text(_json.dumps(saved.to_dict(), ensure_ascii=False), encoding="utf-8")

    live = Session(model_name="deepseek-chat")
    live.add_system("CURRENT PREFIX")
    live.add_user("新问题")
    live.metadata["cwd"] = "/now"
    sctx = SlashContext(
        console=Console(file=StringIO(), width=120),
        session=live, agent_ctx=None, registry=None,
        model_name="deepseek-chat", entry=None, thinking_state={},
    )
    res = handle_slash(f"/resume {p}", sctx)
    assert res.handled and not res.exit_repl
    roles = [m.role for m in sctx.session.messages]
    assert roles == ["system", "user", "assistant", "tool"]  # 整段历史被替换
    assert sctx.session is live  # 对象引用保留
    assert sctx.session.metadata.get("custom") == 1  # 保存的元数据并入


def test_slash_resume_missing_file_reports_error(tmp_path):
    live = Session(model_name="m")
    sctx = SlashContext(
        console=Console(file=StringIO(), width=120),
        session=live, agent_ctx=None, registry=None,
        model_name="m", entry=None, thinking_state={},
    )
    res = handle_slash(f"/resume {tmp_path / 'ghost.json'}", sctx)
    assert res.handled and not res.exit_repl  # 报错但不炸 REPL
    assert sctx.session.messages == []  # 历史未被动过
