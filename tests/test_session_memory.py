"""Session memory — assistant messages keep only the raw *delta*.

Providers echo the full assistant message object back in ``raw_message``;
storing it verbatim duplicates content/tool_calls/reasoning_content for the
whole session lifetime. The only consumer (MiMo adapter) merges keys missing
from ``to_wire()``, so a delta preserves behaviour exactly at a fraction of
the memory.
"""

from __future__ import annotations

from modelbridge.agent.session import Session
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
