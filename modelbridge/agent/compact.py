"""Auto context compression for long REPL sessions.

When session token usage approaches the model's context window, older
history is summarized into a compact form so the conversation can continue
without a manual ``/clear``. Image-bearing messages and tool-call groups
are preserved verbatim; only pure-text runs are summarized.

Invariants honoured here (enforced elsewhere by tests + provider behaviour):

* The leading system prefix (``messages[0..prefix_end)``) is never touched —
  it's the provider's cache-stable prefix.
* An assistant message carrying ``tool_calls`` MUST stay with its following
  ``role=tool`` responses (DeepSeek/Kimi 400 otherwise). The tail boundary
  never starts on a tool message, so a group is never split across the
  middle/tail border.
* Messages containing image blocks are protected — they pass through
  unchanged in their original order; only the contiguous text-only runs
  between them are summarized.
* Failures degrade gracefully: a summarization error leaves the session
  byte-for-byte unchanged and surfaces a notice via ``on_system``.

The module is split into:

* Pure helpers + :func:`compact_session` (no config/network imports at
  module top — the default summarizer calls :func:`client.chat_once`).
* :func:`maybe_auto_compact` — the orchestration entry point the REPL loop
  calls once per turn; it reads config, measures usage, and decides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..client import chat_once, get_model_entry
from ..context.windows import (
    context_window_for,
    estimate_message_tokens,
    estimate_session_tokens,
)
from ..models import ModelEntry
from ..schemas import ChatMessage, text_of
from .session import Session


# ---------------------------------------------------------------------------
# Prompt scaffolding for the summarizer
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = (
    "你是 ModelBridge 的上下文压缩助手。\n"
    "下面是早期对话的逐条记录（每条以 [role] 开头）。"
    "请把它压缩成一份**按重要性整理**的摘要，供模型继续这次会话时参考。\n\n"
    "要求：\n"
    "- 重要的决定、约束、用户意图、关键代码 / 文件 / 命令、错误与教训 —— 保留细节。\n"
    "- 闲聊、重复、中间探索、已被推翻的尝试 —— 压缩成一句话或直接省略。\n"
    "- 保持时间顺序，用简短分条或短段落，不要逐条复述。\n"
    "- 不要解释你在做什么；直接输出摘要正文。\n"
    "- 用与原文相同的语言（中文或英文）。"
)

_SUMMARY_HEADER = "[以下是早期对话的自动摘要，按重要性整理]"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CompactResult:
    """Outcome of one :func:`compact_session` call."""

    ok: bool = True
    skipped: bool = False
    reason: str = ""
    removed_count: int = 0       # messages replaced by summaries
    added_count: int = 0         # summary messages inserted
    saved_tokens: int = 0        # net token reduction (removed − added)


# A summarizer maps a rendered conversation chunk to a summary string.
SummarizerFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Trigger decision
# ---------------------------------------------------------------------------

def should_compact(
    *,
    used_pct: float,
    message_count: int,
    threshold_pct: int,
    min_messages: int,
) -> bool:
    """True when auto-compaction should fire this turn."""
    if used_pct < threshold_pct:
        return False
    if message_count < min_messages:
        return False
    return True


# ---------------------------------------------------------------------------
# Partition helpers (pure)
# ---------------------------------------------------------------------------

def _is_protected(m: ChatMessage) -> bool:
    """Image-bearing messages are never summarized — they pass through."""
    if isinstance(m.content, list):
        for block in m.content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                return True
    return False


def _prefix_end(msgs: list[ChatMessage]) -> int:
    """Index just past the leading run of ``role=system`` messages."""
    i = 0
    while i < len(msgs) and msgs[i].role == "system":
        i += 1
    return i


def _tail_start(msgs: list[ChatMessage], prefix_end: int, keep_turns: int) -> int:
    """Index where the protected recent tail begins.

    Counts ``keep_turns`` user-turns backward from the end (a "turn" = a
    user message + its trailing non-user messages), then snaps back so the
    tail never starts on a ``role=tool`` message (which would orphan it
    from its owning assistant message sitting earlier in the history).
    """
    n = len(msgs)
    if keep_turns <= 0:
        boundary = prefix_end
    else:
        turns = 0
        i = n - 1
        while i >= prefix_end:
            if msgs[i].role == "user":
                turns += 1
                if turns >= keep_turns:
                    break
            i -= 1
        boundary = i if turns >= keep_turns else prefix_end
    # Snap: never start the tail on a tool message.
    while boundary > prefix_end and msgs[boundary].role == "tool":
        boundary -= 1
    return boundary


def _compressible_runs(middle: list[ChatMessage]) -> list[tuple[int, int]]:
    """Half-open ``[start, end)`` ranges of contiguous non-protected messages."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, m in enumerate(middle):
        if _is_protected(m):
            if start is not None:
                runs.append((start, i))
                start = None
        else:
            if start is None:
                start = i
    if start is not None:
        runs.append((start, len(middle)))
    return runs


def _render_run(run: list[ChatMessage]) -> str:
    """Flatten a run of messages into ``[role] text`` lines for the summarizer."""
    lines: list[str] = []
    for m in run:
        text = text_of(m.content)
        if m.reasoning_content:
            rc = m.reasoning_content
            text = f"{text}\n[思考] {rc}" if text else f"[思考] {rc}"
        if m.tool_calls:
            try:
                tc = json.dumps(m.tool_calls, ensure_ascii=False)
                text = f"{text}\n[tool_calls] {tc}" if text else f"[tool_calls] {tc}"
            except (TypeError, ValueError):
                pass
        if not text:
            text = "<空>"
        lines.append(f"[{m.role}] {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core operation
# ---------------------------------------------------------------------------

def _make_default_summarizer(model_name: str) -> SummarizerFn:
    """Build a summarizer that calls the current model via :func:`chat_once`."""

    def _summarize(text: str) -> str:
        _, resp = chat_once(
            text,
            model_name=model_name,
            system=_SUMMARY_SYSTEM,
            timeout=60.0,
            verbose_label="compact",
        )
        return resp.content or ""

    return _summarize


def compact_session(
    session: Session,
    *,
    keep_turns: int,
    model_name: str,
    summarize_fn: SummarizerFn | None = None,
) -> CompactResult:
    """Summarize older history in place.

    See module docstring for the invariants. On any failure returns
    ``ok=False`` and leaves ``session.messages`` exactly as it was.
    """
    msgs = session.messages
    if len(msgs) < 2:
        return CompactResult(skipped=True, reason="历史太短，无需压缩")

    prefix_end = _prefix_end(msgs)
    tail_start = _tail_start(msgs, prefix_end, keep_turns)
    if tail_start <= prefix_end:
        return CompactResult(skipped=True, reason="无可压缩的中段")

    middle = msgs[prefix_end:tail_start]
    runs = _compressible_runs(middle)
    # Only runs with >= 2 messages are worth a model call.
    big_runs = [(s, e) for (s, e) in runs if e - s >= 2]
    if not big_runs:
        return CompactResult(skipped=True, reason="中段无可压缩的文本片段")

    original = list(msgs)  # snapshot for rollback
    summarizer = summarize_fn or _make_default_summarizer(model_name)
    big_starts = {s: (s, e) for (s, e) in big_runs}

    removed_count = 0
    removed_tokens = 0
    added_count = 0
    added_tokens = 0
    new_middle: list[ChatMessage] = []

    try:
        i = 0
        n = len(middle)
        while i < n:
            if i in big_starts:
                s, e = big_starts[i]
                run = middle[s:e]
                before = sum(estimate_message_tokens(m) for m in run)
                summary_text = summarizer(_render_run(run))
                stripped = (summary_text or "").strip()
                if stripped:
                    sm = ChatMessage(
                        role="user",
                        content=f"{_SUMMARY_HEADER}\n{stripped}",
                    )
                    new_middle.append(sm)
                    removed_count += len(run)
                    removed_tokens += before
                    added_count += 1
                    added_tokens += estimate_message_tokens(sm)
                else:
                    # Empty summary — keep the run verbatim rather than lose it.
                    new_middle.extend(run)
                i = e
            else:
                new_middle.append(middle[i])
                i += 1
    except Exception as e:
        session.messages = original
        return CompactResult(ok=False, reason=str(e) or e.__class__.__name__)

    if removed_count == 0:
        return CompactResult(skipped=True, reason="摘要为空，未改动")

    msgs[prefix_end:tail_start] = new_middle
    return CompactResult(
        ok=True,
        removed_count=removed_count,
        added_count=added_count,
        saved_tokens=max(0, removed_tokens - added_tokens),
    )


# ---------------------------------------------------------------------------
# Notice formatting
# ---------------------------------------------------------------------------

def _fmt_tokens(n: int) -> str:
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_notice(result: CompactResult, *, auto: bool = False) -> str:
    """Render a one-line ``[...]`` notice for the REPL / on_system callback."""
    verb = "自动压缩" if auto else "压缩"
    if result.skipped:
        return f"[{verb}：已跳过 — {result.reason}]"
    if not result.ok:
        return f"[{verb}失败：{result.reason}；历史未改动]"
    return (
        f"[已{verb}：移除 {result.removed_count} 条历史，"
        f"净节省约 {_fmt_tokens(result.saved_tokens)} tokens]"
    )


# ---------------------------------------------------------------------------
# Orchestration entry point (used by the REPL loop)
# ---------------------------------------------------------------------------

def maybe_auto_compact(
    session: Session,
    *,
    model_name: str,
    on_system: Callable[[str], None] | None = None,
) -> None:
    """Check config + usage once per turn; compact when the threshold is crossed.

    Self-contained entry point for :func:`agent.loop.run_interactive`. Reads
    config, measures current usage against the active model's window, and
    calls :func:`compact_session` when warranted. Never raises — any failure
    surfaces as a notice via ``on_system`` and the session is left intact.
    """
    try:
        from ..config import load_app_config

        cfg = load_app_config().prompt
        if not cfg.auto_compact:
            return
        entry: ModelEntry = get_model_entry(model_name)
        used = estimate_session_tokens(session.messages)
        window = context_window_for(entry)
        pct = (used / window * 100) if window else 0.0
        if not should_compact(
            used_pct=pct,
            message_count=len(session.messages),
            threshold_pct=cfg.auto_compact_threshold_pct,
            min_messages=cfg.auto_compact_min_messages,
        ):
            return
        result = compact_session(
            session,
            keep_turns=cfg.auto_compact_keep_turns,
            model_name=model_name,
        )
        if on_system is not None and not result.skipped:
            on_system(format_notice(result, auto=True))
    except Exception as e:
        if on_system is not None:
            on_system(f"[自动压缩失败：{e}；历史未改动]")


__all__ = [
    "CompactResult",
    "should_compact",
    "compact_session",
    "maybe_auto_compact",
    "format_notice",
]
