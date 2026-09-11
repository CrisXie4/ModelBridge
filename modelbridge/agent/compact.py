"""Auto context compression for long sessions — seamless checkpoint compaction.

When session token usage approaches the model's context window, older
history is condensed into a structured *checkpoint* so the conversation can
continue without a manual ``/clear``. The design follows the DeepSeek
harness's compaction (``dsh-compaction-basic``), adapted to ModelBridge's
plain message-list session:

* **Checkpoint summary** — the summarizer prompt asks for a fixed 8-section
  Markdown structure (Primary Request / Key Concepts / Files and Code /
  Errors and Fixes / Pending Jobs / Current Work / Next Step / Critical
  Context), framed with ``<compacted-summary>`` tags so later compactions
  recognise prior checkpoints and merge instead of copying them forward.
* **Prefix-cache reuse** — the default summarizer *replays* the
  conversation's own system prefix plus the shadowed messages and appends
  the compaction instruction as the final user message. That request is a
  genuine prefix-extension of the previous routed request, so the
  provider's KV cache stays warm (see the harness's cache-reuse rule).
* **Model-free tool-result pruning** — before any model call, oversized
  *historical* tool results are deterministically rewritten to
  head+marker+tail (the harness's tool-result pruner). This alone often
  relieves enough pressure to skip summarization entirely.
* **Non-shrinking rejection** — a checkpoint whose framed size is not
  smaller than the run it replaces is discarded and the run is kept
  verbatim.

Invariants honoured here (enforced by tests + provider behaviour):

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
  module top — the default summarizer calls the provider directly).
* :func:`maybe_auto_compact` — the orchestration entry point called at
  every step boundary; it reads config, measures usage, and decides.
* :func:`is_context_overflow` — provider-error classification used by the
  agent loop for overflow recovery (compact + retry once).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..client import get_model_entry
from ..context.windows import (
    context_window_for,
    estimate_message_tokens,
    estimate_session_tokens,
)
from ..models import ModelEntry
from ..schemas import ChatMessage, ProviderError, text_of
from .session import Session


# ---------------------------------------------------------------------------
# Prompt scaffolding for the summarizer
# ---------------------------------------------------------------------------

_SUMMARY_HEADER = "[以下是早期对话的自动摘要 — 视为既定背景，直接继续任务]"

#: Tags marking the checkpoint body. Later compactions see prior checkpoints
#: through these tags and merge them instead of copying forward.
_SUMMARY_OPEN_TAG = "<compacted-summary>"
_SUMMARY_CLOSE_TAG = "</compacted-summary>"

#: The compaction directive, delivered as the FINAL user message after the
#: replayed conversation rather than as a distinct summarizer system prompt.
#: Keeping the conversation's own system prompt and message prefix in front
#: of it makes the auxiliary call a genuine prefix of the last routed
#: request, so the provider's KV cache is reused instead of invalidated.
_COMPACTION_INSTRUCTION = (
    "You are now acting as a compaction engine for this AI coding assistant. "
    "Condense the conversation ABOVE into a structured checkpoint that lets "
    "another model resume the work with no loss of essential context.\n\n"
    "Output EXACTLY the Markdown structure below: keep every section, in "
    "order. Use terse bullets, not prose paragraphs. Write \"(none)\" for an "
    "empty section — never drop a section.\n\n"
    "## Primary Request and Intent\n"
    "- [the user's original and evolving goals; quote verbatim where the exact wording matters]\n\n"
    "## Key Technical Concepts\n"
    "- [technologies, frameworks, patterns, and conventions in play]\n\n"
    "## Files and Code\n"
    "- [exact path: why it matters, key changes or snippets]\n\n"
    "## Errors and Fixes\n"
    "- [error: how it was resolved, plus any related user feedback]\n\n"
    "## Pending Jobs\n"
    "- [explicitly requested work not yet completed]\n\n"
    "## Current Work\n"
    "- [precisely what was in progress at this checkpoint]\n\n"
    "## Next Step\n"
    "- [the single next action, directly in line with the most recent request, or \"(none)\"]\n\n"
    "## Critical Context\n"
    "- [decisions and their rationale, constraints, user preferences, open questions]\n\n"
    "Rules:\n"
    "- Write in the same language as the conversation (中文对话用中文，英文对话用英文).\n"
    "- Preserve exact file paths, commands, error strings, identifiers, numeric values, and function signatures.\n"
    "- Capture user feedback and explicit instructions faithfully, especially corrections.\n"
    "- Do NOT mention this summarization request or that the context was compacted.\n"
    "- Output only the checkpoint text: do not call any tool or take any other action.\n"
    f"- If the conversation already contains a {_SUMMARY_OPEN_TAG} block, it is a PRIOR "
    "checkpoint. Do not copy it forward verbatim: preserve still-true facts, "
    "drop stale ones, and merge newer information into one consolidated summary."
)

#: Cap on the summary generation — checkpoints are terse by construction.
_SUMMARY_MAX_TOKENS = 4096

#: Pruning budgets (chars) for oversized historical tool results — the
#: model-free first pass, mirroring dsh-compaction-tool-result-pruner.
_PRUNE_HEAD_CHARS = 1500
_PRUNE_TAIL_CHARS = 400


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
    saved_tokens: int = 0        # net token reduction (removed − added + pruned)
    pruned_count: int = 0        # tool results shrunk by model-free pruning


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
# Overflow classification (agent-loop recovery)
# ---------------------------------------------------------------------------

#: Lowercased substrings providers use when the *prompt* exceeds the window.
_OVERFLOW_MARKERS = (
    "context length",
    "context window",
    "context_window",
    "maximum context",
    "max context",
    "too many tokens",
    "prompt is too long",
    "prompt too long",
    "input tokens",
    "input too long",
    "exceeds the context",
    "reduce the length",
    "reduce your prompt",
    "上下文长度",
    "超过上下文",
    "输入过长",
    "超出上下文",
)


def is_context_overflow(err: BaseException) -> bool:
    """True when a provider error means the request exceeded the context window.

    Used by the agent loop for overflow recovery: compact once, then retry.
    Deliberately narrow — a generic 400 must NOT trigger a compaction retry.
    """
    if not isinstance(err, ProviderError):
        return False
    text = f"{err.message or ''} {err.hint or ''}".lower()
    return any(m in text for m in _OVERFLOW_MARKERS)


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
# Model-free tool-result pruning
# ---------------------------------------------------------------------------

def prune_tool_results(
    messages: list[ChatMessage],
    *,
    threshold_chars: int,
) -> int:
    """Replace oversized ``role=tool`` text results with head+marker+tail.

    Deterministic and model-free — the cheapest pressure relief available
    (mirrors the harness's tool-result pruner). Only *historical* results
    should be passed in: the caller compacts the middle region while the
    recent tail stays verbatim. Entries are *replaced* in the list (never
    mutated in place) so a caller's earlier snapshot stays a valid rollback
    state. Returns the number of characters removed.
    """
    if threshold_chars <= 0:
        return 0
    removed = 0
    for i, m in enumerate(messages):
        if m.role != "tool" or not isinstance(m.content, str):
            continue
        if len(m.content) <= threshold_chars:
            continue
        before = len(m.content)
        head = m.content[:_PRUNE_HEAD_CHARS]
        tail = m.content[-_PRUNE_TAIL_CHARS:]
        cut = before - _PRUNE_HEAD_CHARS - _PRUNE_TAIL_CHARS
        pruned = f"{head}\n[…中间已剪裁 {cut} 字符，如仍需要请重新调用工具…]\n{tail}"
        messages[i] = ChatMessage(
            role="tool",
            tool_call_id=m.tool_call_id,
            name=m.name,
            content=pruned,
        )
        removed += before - len(pruned)
    return removed


# ---------------------------------------------------------------------------
# Core operation
# ---------------------------------------------------------------------------

def _replay_summarize(
    model_name: str,
    *,
    system_messages: list[ChatMessage],
    run: list[ChatMessage],
) -> str:
    """Default summarizer: replay prefix + run, append the directive.

    The request reuses the conversation's own system prefix and the shadowed
    messages verbatim, so it is a prefix-extension of the previous routed
    request — the provider's prefix cache stays warm (byte-stable prefix
    rule; see cache.affinity and the harness's compaction summarizer).
    """
    from ..providers import get_provider
    from ..schemas import ChatRequest

    entry = get_model_entry(model_name)
    provider = get_provider(entry)
    messages = [
        *system_messages,
        *run,
        ChatMessage(role="user", content=_COMPACTION_INSTRUCTION),
    ]
    request = ChatRequest(
        model=entry.model,
        messages=messages,
        max_tokens=_SUMMARY_MAX_TOKENS,
    )
    resp = provider.chat(request, timeout=120.0, verbose_label="compact")
    return (resp.content or "").strip()


def _frame_checkpoint(summary_text: str) -> str:
    """Wrap a summary body in the durable checkpoint framing."""
    return (
        f"{_SUMMARY_HEADER}\n"
        f"{_SUMMARY_OPEN_TAG}\n{summary_text.strip()}\n{_SUMMARY_CLOSE_TAG}"
    )


def compact_session(
    session: Session,
    *,
    keep_turns: int,
    model_name: str,
    summarize_fn: SummarizerFn | None = None,
    prune_chars: int = 0,
    on_started: Callable[[], None] | None = None,
) -> CompactResult:
    """Condense older history in place.

    See module docstring for the invariants. On any failure returns
    ``ok=False`` and leaves ``session.messages`` exactly as it was.
    ``prune_chars > 0`` first rewrites oversized historical tool results
    (model-free) — that pass is part of the same atomic operation and rolls
    back with everything else.

    Two-phase protocol (opencode's started/ended shape, right-sized for an
    in-memory session): ``on_started`` fires right before the first
    summarizer call — the slow, fallible part — while the visible session
    only ever changes at the single atomic commit at the end. A crash or
    error before that commit is equivalent to "compaction never started":
    the previous history boundary stays intact. After a successful commit
    the system-context snapshot is invalidated (``session.metadata``), so
    the next turn re-baselines environment facts the checkpoint may have
    summarized away — see :mod:`modelbridge.context.system_context`.
    """
    msgs = session.messages
    if len(msgs) < 2:
        return CompactResult(skipped=True, reason="历史太短，无需压缩")

    prefix_end = _prefix_end(msgs)
    tail_start = _tail_start(msgs, prefix_end, keep_turns)
    if tail_start <= prefix_end:
        return CompactResult(skipped=True, reason="无可压缩的中段")

    middle = msgs[prefix_end:tail_start]

    # Pass 0 — model-free pruning of bulky historical tool results.
    pruned_chars = prune_tool_results(middle, threshold_chars=prune_chars)
    pruned_tokens = max(0, pruned_chars // 3)  # rough CJK/ASCII mix

    runs = _compressible_runs(middle)
    # Only runs with >= 2 messages are worth a model call.
    big_runs = [(s, e) for (s, e) in runs if e - s >= 2]
    if not big_runs:
        if pruned_chars:
            # Pruning alone relieved pressure — commit the pruned middle.
            msgs[prefix_end:tail_start] = middle
            return CompactResult(
                ok=True,
                removed_count=0,
                added_count=0,
                saved_tokens=pruned_tokens,
                pruned_count=sum(
                    1 for m in middle
                    if m.role == "tool" and "已剪裁" in (m.content or "")
                ),
            )
        return CompactResult(skipped=True, reason="中段无可压缩的文本片段")

    original = list(msgs)  # snapshot for rollback (includes pre-prune state)
    big_starts = {s: (s, e) for (s, e) in big_runs}
    system_messages = msgs[:prefix_end]

    # Phase marker: the summarizer calls below are the slow, fallible part.
    # Nothing becomes model-visible until the single atomic commit below.
    if on_started is not None:
        try:
            on_started()
        except Exception:
            pass

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
                run_tokens = sum(estimate_message_tokens(m) for m in run)
                if summarize_fn is not None:
                    summary_text = summarize_fn(_render_run(run))
                else:
                    summary_text = _replay_summarize(
                        model_name, system_messages=system_messages, run=run
                    )
                stripped = (summary_text or "").strip()
                framed = _frame_checkpoint(stripped) if stripped else ""
                if framed:
                    sm = ChatMessage(role="user", content=framed)
                    framed_tokens = estimate_message_tokens(sm)
                    if framed_tokens >= run_tokens:
                        # Non-shrinking summary — keep the run verbatim
                        # rather than replace content with something bigger.
                        new_middle.extend(run)
                    else:
                        new_middle.append(sm)
                        removed_count += len(run)
                        removed_tokens += run_tokens
                        added_count += 1
                        added_tokens += framed_tokens
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

    if removed_count == 0 and pruned_chars == 0:
        return CompactResult(skipped=True, reason="摘要未变小，未改动")

    msgs[prefix_end:tail_start] = new_middle
    # Invalidate the system-context snapshot: the checkpoint may have
    # summarized away environment facts (date, git branch). Clearing it
    # makes the next turn re-emit a fresh <system-update> baseline.
    session.metadata.pop("system_context_snapshot", None)
    return CompactResult(
        ok=True,
        removed_count=removed_count,
        added_count=added_count,
        saved_tokens=max(0, removed_tokens - added_tokens) + pruned_tokens,
        pruned_count=sum(
            1 for m in new_middle
            if m.role == "tool" and "已剪裁" in (m.content or "")
        ),
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
    bits = []
    if result.removed_count:
        bits.append(f"摘要替代 {result.removed_count} 条历史")
    if result.pruned_count:
        bits.append(f"剪裁 {result.pruned_count} 条工具输出")
    detail = "，".join(bits) if bits else "仅剪裁"
    return f"[已{verb}：{detail}，净节省约 {_fmt_tokens(result.saved_tokens)} tokens]"


# ---------------------------------------------------------------------------
# Orchestration entry point (used at every step boundary)
# ---------------------------------------------------------------------------

def maybe_auto_compact(
    session: Session,
    *,
    model_name: str,
    on_system: Callable[[str], None] | None = None,
    force: bool = False,
) -> CompactResult | None:
    """Check config + usage; compact when the threshold is crossed.

    Self-contained entry point used by :func:`agent.loop.run_agent_turn`
    (every step boundary) and the REPL (after each turn). Reads config,
    measures current usage against the active model's window, and calls
    :func:`compact_session` when warranted. ``force=True`` (overflow
    recovery) bypasses the threshold / min-message gates but still respects
    ``auto_compact`` as the master switch. Never raises — any failure
    surfaces as a notice via ``on_system`` and the session is left intact.
    """
    try:
        from ..config import load_app_config

        cfg = load_app_config().prompt
        if not cfg.auto_compact:
            return None
        # Cheap gate first — a session under the minimum message count can
        # never compact, and skipping here avoids both the model lookup and
        # any spurious failure notice on tiny sessions.
        if not force and len(session.messages) < cfg.auto_compact_min_messages:
            return None
        entry: ModelEntry = get_model_entry(model_name)
        used = estimate_session_tokens(session.messages)
        window = context_window_for(entry)
        pct = (used / window * 100) if window else 0.0
        if not force and not should_compact(
            used_pct=pct,
            message_count=len(session.messages),
            threshold_pct=cfg.auto_compact_threshold_pct,
            min_messages=cfg.auto_compact_min_messages,
        ):
            return None
        result = compact_session(
            session,
            keep_turns=cfg.auto_compact_keep_turns,
            model_name=model_name,
            prune_chars=cfg.auto_compact_prune_chars,
            on_started=(
                (lambda: on_system("[自动压缩：正在生成检查点，历史暂时只读…]"))
                if on_system is not None
                else None
            ),
        )
        if on_system is not None and not result.skipped:
            on_system(format_notice(result, auto=True))
        return result
    except Exception as e:
        if on_system is not None:
            on_system(f"[自动压缩失败：{e}；历史未改动]")
        return None


__all__ = [
    "CompactResult",
    "SummarizerFn",
    "should_compact",
    "is_context_overflow",
    "prune_tool_results",
    "compact_session",
    "maybe_auto_compact",
    "format_notice",
]
