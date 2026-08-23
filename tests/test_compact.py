"""Auto context compression — partitioning, checkpointing, pruning, rollback.

Covers the invariants documented in :mod:`modelbridge.agent.compact`:

* The leading system prefix is preserved.
* An assistant message with ``tool_calls`` is never split from its
  ``role=tool`` responses (the tail boundary snaps back).
* Image-bearing (multimodal) messages pass through unchanged.
* A summarizer failure leaves the session byte-for-byte intact.
* ``should_compact`` honours the threshold + min-messages gates.
* The checkpoint framing wraps summaries in ``<compacted-summary>`` tags.
* A summary that isn't smaller than the run it replaces is rejected
  (the run is kept verbatim).
* Model-free tool-result pruning rewrites oversized historical results.
* ``is_context_overflow`` classifies provider errors for retry recovery.
"""

from __future__ import annotations

from modelbridge.agent.compact import (
    _compressible_runs,
    _frame_checkpoint,
    _is_protected,
    _prefix_end,
    _tail_start,
    compact_session,
    is_context_overflow,
    prune_tool_results,
    should_compact,
)
from modelbridge.agent.session import Session
from modelbridge.schemas import ChatMessage, ProviderError


# ---------------------------------------------------------------------------
# Helpers for building message lists
# ---------------------------------------------------------------------------

#: Realistic filler so a run's token estimate comfortably exceeds the framed
#: checkpoint (whose preamble costs a fixed ~70 tokens). Tiny "u1"/"a1"
#: strings are *supposed* to fail the shrink check now — that's the guard.
_BIG = "关于项目结构的说明。" * 40  # ~400 CJK chars ≈ 400+ heuristic tokens


def _sys(content: str = "SYSTEM") -> ChatMessage:
    return ChatMessage(role="system", content=content)


def _user(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)


def _asst(content: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=content)


def _asst_tc(call_ids: list[str], content: str = "") -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=content,
        tool_calls=[
            {"id": cid, "type": "function", "function": {"name": "f", "arguments": "{}"}}
            for cid in call_ids
        ],
    )


def _tool(call_id: str, content: str = "r") -> ChatMessage:
    return ChatMessage(role="tool", tool_call_id=call_id, content=content)


def _user_img(text: str) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=[
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ],
    )


def _fake_summarizer(marker: str = "SUMMARY"):
    """A summarizer that returns a fixed string — no network."""
    def _s(_text: str) -> str:
        return marker
    return _s


def _explode_summarizer():
    """A summarizer that always raises, to exercise rollback."""
    def _s(_text: str) -> str:
        raise RuntimeError("boom")
    return _s


# ---------------------------------------------------------------------------
# should_compact
# ---------------------------------------------------------------------------

def test_should_compact_threshold_boundary():
    assert not should_compact(
        used_pct=79.9, message_count=100, threshold_pct=80, min_messages=20
    )
    assert should_compact(
        used_pct=80.0, message_count=100, threshold_pct=80, min_messages=20
    )


def test_should_compact_min_messages_gate():
    # Under min_messages even if over threshold.
    assert not should_compact(
        used_pct=99.0, message_count=19, threshold_pct=80, min_messages=20
    )
    assert should_compact(
        used_pct=99.0, message_count=20, threshold_pct=80, min_messages=20
    )


# ---------------------------------------------------------------------------
# Pure partition helpers
# ---------------------------------------------------------------------------

def test_prefix_end_single_and_multiple_system():
    assert _prefix_end([_user("a")]) == 0
    assert _prefix_end([_sys(), _user("a")]) == 1
    assert _prefix_end([_sys("s1"), _sys("s2"), _user("a")]) == 2


def test_is_protected_text_vs_image():
    assert _is_protected(_user("hi")) is False
    assert _is_protected(_user_img("look")) is True
    assert _is_protected(_asst("ok")) is False


def test_compressible_runs_splits_around_images():
    middle = [_user("a"), _asst("b"), _user_img("pic"), _user("c"), _asst("d")]
    runs = _compressible_runs(middle)
    assert runs == [(0, 2), (3, 5)]


def test_compressible_runs_all_text_is_one_run():
    middle = [_user("a"), _asst("b"), _user("c")]
    assert _compressible_runs(middle) == [(0, 3)]


# ---------------------------------------------------------------------------
# tail boundary: turn counting + tool-group atomicity
# ---------------------------------------------------------------------------

def test_tail_start_keeps_n_user_turns():
    msgs = [
        _sys(),
        _user("u1"), _asst("a1"),
        _user("u2"), _asst("a2"),
        _user("u3"), _asst("a3"),
    ]
    # keep 2 turns → tail starts at u2 (index 3).
    assert _tail_start(msgs, prefix_end=1, keep_turns=2) == 3


def test_tail_start_not_enough_turns_returns_prefix_end():
    msgs = [_sys(), _user("u1"), _asst("a1")]
    # Want 5 turns, only 1 exists → everything non-system is the middle boundary.
    assert _tail_start(msgs, prefix_end=1, keep_turns=5) == 1


def test_tail_start_snaps_off_tool_message():
    msgs = [
        _sys(),
        _user("u1"), _asst("a1"),
        _asst_tc(["c1"]), _tool("c1"),   # assistant(tool_calls) + tool response
        _user("u2"),
    ]
    msgs2 = [
        _sys(),
        _asst_tc(["c1"]), _tool("c1"),  # group right after prefix
        _user("u1"),
    ]
    assert _tail_start(msgs2, prefix_end=1, keep_turns=1) == 3
    # And confirm the real straddle case keeps the group in the tail:
    assert _tail_start(msgs, prefix_end=1, keep_turns=1) == 5  # u2 stays the boundary


# ---------------------------------------------------------------------------
# checkpoint framing
# ---------------------------------------------------------------------------

def test_frame_checkpoint_wraps_tags_and_header():
    framed = _frame_checkpoint("第一段摘要")
    assert "<compacted-summary>" in framed
    assert "</compacted-summary>" in framed
    assert "第一段摘要" in framed
    assert "自动摘要" in framed


# ---------------------------------------------------------------------------
# compact_session: structure after compression
# ---------------------------------------------------------------------------

def test_compact_session_replaces_middle_with_summary():
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user(_BIG + " u1"), _asst(_BIG + " a1"),
        _user(_BIG + " u2"), _asst(_BIG + " a2"),
        _user("u3"), _asst("a3"),   # tail (keep_turns=1)
    ]
    result = compact_session(
        session,
        keep_turns=1,
        model_name="fake",
        summarize_fn=_fake_summarizer("EARLY TALK"),
    )
    assert result.ok and not result.skipped
    assert result.removed_count == 4  # u1,a1,u2,a2
    # Structure: system, summary(user), u3, a3
    roles = [m.role for m in session.messages]
    assert roles == ["system", "user", "user", "assistant"]
    assert "EARLY TALK" in session.messages[1].content
    assert "自动摘要" in session.messages[1].content
    assert "<compacted-summary>" in session.messages[1].content
    assert result.saved_tokens > 0


def test_compact_session_preserves_system_prefix_verbatim():
    session = Session(model_name="fake")
    session.messages = [
        _sys("MY PREFIX"),
        _user(_BIG + " u1"), _asst(_BIG + " a1"),
        _user("u2"), _asst("a2"),
    ]
    compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=_fake_summarizer()
    )
    assert session.messages[0].role == "system"
    assert session.messages[0].content == "MY PREFIX"


def test_compact_session_keeps_tail_tool_group_intact():
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user(_BIG + " u1"), _asst(_BIG + " a1"),
        _user("u2"), _asst_tc(["c1", "c2"]), _tool("c1", "r1"), _tool("c2", "r2"),
    ]
    compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=_fake_summarizer()
    )
    # The tail's assistant(tool_calls) + both tool messages survive together.
    tail = session.messages[2:]  # after system + summary
    roles = [m.role for m in tail]
    assert roles == ["user", "assistant", "tool", "tool"]
    asst = tail[1]
    assert asst.tool_calls is not None and len(asst.tool_calls) == 2
    tool_ids = {m.tool_call_id for m in tail if m.role == "tool"}
    assert tool_ids == {"c1", "c2"}


def test_compact_session_preserves_image_messages_in_place():
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user(_BIG + " u1"), _asst(_BIG + " a1"),
        _user_img("pic1"),              # protected — stays verbatim
        _user(_BIG + " u2"), _asst(_BIG + " a2"),
        _user("u3"), _asst("a3"),       # tail
    ]
    result = compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=_fake_summarizer()
    )
    assert result.ok
    # The image message is still present, still multimodal, in its original order.
    imgs = [m for m in session.messages if isinstance(m.content, list)]
    assert len(imgs) == 1
    assert any(b.get("type") == "image_url" for b in imgs[0].content)
    # Two text runs (before & after the image) → two summaries.
    summaries = [m for m in session.messages if "自动摘要" in (m.content if isinstance(m.content, str) else "")]
    assert len(summaries) == 2


def test_compact_session_too_short_is_skipped():
    session = Session(model_name="fake")
    session.messages = [_sys(), _user("only")]
    result = compact_session(
        session, keep_turns=6, model_name="fake", summarize_fn=_fake_summarizer()
    )
    assert result.skipped
    assert session.messages == [_sys(), _user("only")]


def test_compact_session_no_compressible_run_is_skipped():
    # Only protected messages in the middle.
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user_img("p1"), _user_img("p2"),  # each its own run of length 0/1
        _user("u"), _asst("a"),             # tail
    ]
    result = compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=_fake_summarizer()
    )
    assert result.skipped


def test_compact_session_summarizer_failure_rolls_back():
    session = Session(model_name="fake")
    original = [
        _sys(),
        _user(_BIG + " u1"), _asst(_BIG + " a1"),
        _user(_BIG + " u2"), _asst(_BIG + " a2"),
        _user("u3"), _asst("a3"),
    ]
    session.messages = list(original)
    # Deep-copy-free rollback check: mutate the middle with pruning too, so
    # the rollback of the pruning pass is exercised in the same test.
    result = compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=_explode_summarizer(),
        prune_chars=100,
    )
    assert not result.ok
    # Session is byte-for-byte unchanged.
    assert session.messages == original


def test_compact_session_empty_summary_keeps_run_verbatim():
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user(_BIG + " u1"), _asst(_BIG + " a1"),
        _user(_BIG + " u2"), _asst(_BIG + " a2"),
        _user("u3"), _asst("a3"),
    ]
    result = compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=lambda _t: "   "
    )
    # Empty summary → nothing removed → skipped.
    assert result.skipped


def test_compact_session_nonshrinking_summary_keeps_run_verbatim():
    # A "summary" that's longer than the tiny run it replaces must NOT land.
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user("u1"), _asst("a1"),            # tiny run (~10 tokens)
        _user("u3"), _asst("a3"),            # tail
    ]
    result = compact_session(
        session, keep_turns=1, model_name="fake",
        summarize_fn=lambda _t: _BIG + _BIG,  # huge "summary"
    )
    assert result.skipped
    assert "u1" in session.messages[1].content  # run kept verbatim


# ---------------------------------------------------------------------------
# model-free tool-result pruning
# ---------------------------------------------------------------------------

def test_prune_tool_results_rewrites_oversized_only():
    big = "x" * 10_000
    msgs = [
        _user("q"),
        _tool("c1", big),
        _tool("c2", "small"),
    ]
    removed = prune_tool_results(msgs, threshold_chars=4000)
    assert removed > 8000
    assert len(msgs[1].content) < 2500
    assert "已剪裁" in msgs[1].content
    assert msgs[1].content.startswith("x" * 100)
    assert msgs[1].content.endswith("x" * 100)
    assert msgs[2].content == "small"      # under threshold — untouched
    assert msgs[0].content == "q"          # non-tool — untouched


def test_prune_tool_results_zero_threshold_disables():
    msgs = [_tool("c1", "x" * 10_000)]
    assert prune_tool_results(msgs, threshold_chars=0) == 0
    assert len(msgs[0].content) == 10_000


def test_compact_session_prune_only_relief_commits():
    # A middle whose only compressible run is a single message (below the
    # 2-message model-call floor) but carries a huge tool result: pruning
    # alone commits and reports savings, with no summarizer call.
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _tool("c0", "z" * 12_000),      # single-message middle
        _user("u"), _asst("a"),         # tail
    ]
    result = compact_session(
        session, keep_turns=1, model_name="fake",
        summarize_fn=_explode_summarizer(),  # must NOT be called
        prune_chars=4000,
    )
    assert result.ok and result.pruned_count == 1
    assert result.saved_tokens > 0
    assert len(session.messages[1].content) < 2500


def test_compact_session_pruning_rolls_back_with_summary_failure():
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user("q1"), _asst_tc(["c1"]), _tool("c1", "y" * 12_000),
        _user("u"), _asst("a"),         # tail
    ]
    result = compact_session(
        session, keep_turns=1, model_name="fake",
        summarize_fn=_explode_summarizer(),
        prune_chars=4000,
    )
    assert not result.ok
    # The pruned tool result is restored byte-for-byte with the rest.
    assert len(session.messages[3].content) == 12_000


# ---------------------------------------------------------------------------
# overflow classification
# ---------------------------------------------------------------------------

def _perr(msg: str) -> ProviderError:
    return ProviderError(msg, provider="p")


def test_is_context_overflow_matches_provider_wordings():
    assert is_context_overflow(_perr(
        "This model's maximum context length is 65536 tokens. "
        "However, you requested 100000 tokens"
    ))
    assert is_context_overflow(_perr("prompt is too long: 9000 tokens > 8000 limit"))
    assert is_context_overflow(_perr("输入过长：超出上下文窗口"))
    assert is_context_overflow(_perr("Request too many tokens (8000 > 4096)"))


def test_is_context_overflow_rejects_unrelated_errors():
    assert not is_context_overflow(_perr("401 unauthorized"))
    assert not is_context_overflow(_perr("connection reset by peer"))
    assert not is_context_overflow(RuntimeError("context length?"))
    assert not is_context_overflow(_perr("model overloaded, retry later"))


# ---------------------------------------------------------------------------
# rollback: compaction mid-flight preserves tool groups
# ---------------------------------------------------------------------------

def test_compact_session_never_splits_tool_group_from_replay():
    # The compressible middle contains assistant(tool_calls)+tool pairs; the
    # replacement keeps them inside ONE run so a replay summarizer request
    # (system + run + instruction) stays provider-legal.
    session = Session(model_name="fake")
    session.messages = [
        _sys(),
        _user(_BIG + " u1"),
        _asst_tc(["c1"]), _tool("c1", _BIG),
        _user(_BIG + " u2"), _asst(_BIG + " a2"),
        _user("u3"), _asst("a3"),
    ]
    def spy_summarizer(text: str) -> str:
        return "SPY " + text[:50]

    compact_session(
        session, keep_turns=1, model_name="fake", summarize_fn=spy_summarizer
    )
    assert session.messages[1].role == "user"
    # tail intact
    assert [m.role for m in session.messages[-2:]] == ["user", "assistant"]
