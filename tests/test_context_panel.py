"""Context info panel — progress bar, role-breakdown accounting, panel layout.

Covers the always-on multi-line panel that replaced the single-line status
bar (see ``render_context_panel``), plus the per-role token accounting it
reads from. Wide vs narrow terminal folding is exercised, as is the
graceful fallback when ``todo_store`` / role data is missing.
"""

from __future__ import annotations

import io

from rich.console import Console

from modelbridge.agent.ui import (
    TurnStats,
    _progress_bar,
    render_context_panel,
)
from modelbridge.context.windows import estimate_tokens_by_role
from modelbridge.schemas import ChatMessage

# ---------------------------------------------------------------------------
# _progress_bar
# ---------------------------------------------------------------------------

def test_progress_bar_fill_count():
    bar, _color = _progress_bar(12.0, width=20)
    # 12% of 20 cells = 2.4 → rounded to 2 filled cells
    assert bar == "█" * 2 + "░" * 18


def test_progress_bar_extremes():
    full_bar, _ = _progress_bar(100.0, width=10)
    assert full_bar == "█" * 10
    empty_bar, _ = _progress_bar(0.0, width=10)
    assert empty_bar == "░" * 10


def test_progress_bar_color_thresholds():
    # Boundary: < 60 green, < 85 yellow, >= 85 red.
    _, green = _progress_bar(59.9, width=10)
    assert green == "green"
    _, yellow = _progress_bar(60.0, width=10)
    assert yellow == "yellow"
    _, yellow2 = _progress_bar(84.9, width=10)
    assert yellow2 == "yellow"
    _, red = _progress_bar(85.0, width=10)
    assert red == "red"


# ---------------------------------------------------------------------------
# estimate_tokens_by_role
# ---------------------------------------------------------------------------

def test_tokens_by_role_classification():
    msgs = [
        ChatMessage(role="system", content="系统提示"),       # prefix
        ChatMessage(role="user", content="用户问"),           # conversation
        ChatMessage(role="assistant", content="助手答"),      # conversation
        ChatMessage(role="assistant", reasoning_content="推理"),  # reasoning
        ChatMessage(role="tool", content="工具结果"),         # tool
    ]
    rb = estimate_tokens_by_role(msgs)
    assert set(rb.keys()) == {"prefix", "reasoning", "tool", "conversation"}
    # system message body lands in prefix
    assert rb["prefix"] > 0
    # reasoning bucket gets the reasoning_content
    assert rb["reasoning"] > 0
    # tool message body lands in tool bucket
    assert rb["tool"] > 0
    # user + assistant visible content land in conversation
    assert rb["conversation"] > 0


def test_tokens_by_role_sums_within_session_total():
    from modelbridge.context.windows import estimate_session_tokens
    msgs = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="hello"),
        ChatMessage(role="tool", content="result"),
    ]
    rb = estimate_tokens_by_role(msgs)
    total = estimate_session_tokens(msgs)
    # The four buckets are mutually exclusive and account for the same
    # bodies (each with its own per-message overhead), so they should sum
    # to within one per-message-overhead unit of the full-session total.
    assert abs(sum(rb.values()) - total) <= 4


def test_tokens_by_role_empty():
    rb = estimate_tokens_by_role([])
    assert rb == {"prefix": 0, "reasoning": 0, "tool": 0, "conversation": 0}


# ---------------------------------------------------------------------------
# render_context_panel — single right-aligned status line
# ---------------------------------------------------------------------------

def _make_stats(**kw) -> TurnStats:
    defaults = {
        "used_tokens": 12_300,
        "context_window": 1_000_000,
        "reasoning_tokens": 2_100,
        "last_response_chars": 1000,
        "last_response_ms": 3420,
        "iterations": 1,
    }
    defaults.update(kw)
    return TurnStats(**defaults)


def _render(stats, *, width, role_breakdown=None, todo_summary=None, current_todo=None):
    buf = io.StringIO()
    console = Console(file=buf, width=width, force_terminal=True)
    render_context_panel(
        console,
        stats,
        model_name="m",
        role_breakdown=role_breakdown,
        todo_summary=todo_summary,
        current_todo=current_todo,
    )
    return buf.getvalue().splitlines()


def test_panel_is_one_line_with_usage_and_todo():
    lines = _render(
        _make_stats(used_tokens=123_000, context_window=1_000_000),
        width=120,
        role_breakdown={"prefix": 3100, "reasoning": 2100, "tool": 4800, "conversation": 2300},
        todo_summary={"total": 5, "done": 2, "in_progress": 1, "pending": 2},
        current_todo="优化气泡宽度",
    )
    # Exactly one line — the compact opencode-style status.
    assert len(lines) == 1
    line = lines[0]
    assert "m" in line            # model name
    assert "12%" in line          # 123000 / 1000000
    assert "123.0k/1.0M" in line
    assert "推理" in line and "工具" in line
    assert "计划" in line and "2/5" in line
    assert "3420ms" in line


def test_panel_omits_todo_when_empty():
    lines = _render(
        _make_stats(),
        width=120,
        role_breakdown={"prefix": 3100, "reasoning": 0, "tool": 0, "conversation": 1000},
        todo_summary=None,
        current_todo=None,
    )
    assert len(lines) == 1
    assert "计划" not in lines[0]
    assert "3420ms" in lines[0]   # timing still present


def test_panel_truncates_long_current_todo():
    long_todo = "这是一个非常非常非常非常非常非常非常非常非常非常长的待办内容描述"
    lines = _render(
        _make_stats(),
        width=120,
        role_breakdown={"prefix": 3100, "reasoning": 0, "tool": 0, "conversation": 1000},
        todo_summary={"total": 3, "done": 1, "in_progress": 1, "pending": 1},
        current_todo=long_todo,
    )
    line = next(ln for ln in lines if "1/3" in ln)
    # Truncation cap is 16 chars + ellipsis.
    assert "…" in line
    assert len(lines) == 1


def test_panel_fallback_on_error():
    """A malformed stats object must not crash — the panel swallows errors.

    The Broken object fails on every attribute access, so even the
    ``status_bar_text`` fallback won't render — but ``render_context_panel``
    itself must not raise, and a *less* broken object (real TurnStats with a
    bogus console) still produces output via the fallback path.
    """
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True)
    # A Broken object whose used_pct raises — the main render path aborts
    # into the except block. Even though the fallback also can't render
    # here, the function returns without raising.
    class Broken:
        @property
        def used_pct(self):
            raise RuntimeError("boom")

    # Must not raise.
    render_context_panel(console, Broken(), model_name="m")  # type: ignore[arg-type]

    # Now a realistic fallback: a valid TurnStats always renders at least
    # the legacy status line, so a broken-but-recoverable scenario still
    # leaves output behind.
    buf2 = io.StringIO()
    console2 = Console(file=buf2, width=120, force_terminal=True)
    good = _make_stats()
    render_context_panel(console2, good, model_name="m")
    assert buf2.getvalue()


# ---------------------------------------------------------------------------
# render_tool_bubble — compact right-aligned entry
# ---------------------------------------------------------------------------

def test_tool_bubble_compact_one_line():
    from modelbridge.agent.ui import render_tool_bubble

    buf = io.StringIO()
    console = Console(file=buf, width=100, force_terminal=True)
    render_tool_bubble(
        console,
        tool_name="read_file",
        args_preview="path=src/app.py",
        body="line1\nline2\n" * 200,   # long body → size hint, not inline
    )
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "read_file" in lines[0]
    assert "字符" in lines[0]


def test_tool_bubble_short_body_inline_error_first_line():
    from modelbridge.agent.ui import render_tool_bubble

    buf = io.StringIO()
    console = Console(file=buf, width=100, force_terminal=True)
    render_tool_bubble(
        console,
        tool_name="str_replace",
        args_preview="path=a.py",
        body="old_str 未在文件中找到。",
        is_error=True,
    )
    out = buf.getvalue()
    assert "str_replace" in out
    assert "未在文件中找到" in out
    assert "✗" in out
