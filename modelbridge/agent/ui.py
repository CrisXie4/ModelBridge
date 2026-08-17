"""Chat-bubble REPL UI helpers.

* :func:`render_user_bubble`     — user input in a right-aligned panel.
* :func:`render_tool_bubble`     — tool call/result on the left.
* :func:`status_bar_text`        — build the status-line as a Text object.
* :class:`AssistantStream`       — context manager that prints a left
  panel and refreshes it live as content/reasoning deltas arrive.

Everything here is rich-only — no I/O or model calls. Easy to unit-test
and easy to swap for a future web UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from ..context.windows import (
    context_window_for,
    estimate_reasoning_tokens,
    estimate_session_tokens,
)
from ..cost.estimator import estimate_tokens
from ..models import ModelEntry
from ..schemas import ChatMessage, ChatResponse


# ---------------------------------------------------------------------------
# Layout knobs
# ---------------------------------------------------------------------------

#: Bubble takes up most of the console width on wide terminals (full-screen
#: friendly — leaves ~6% total margin, capped at 200 cols), but stays narrow
#: on tiny windows so text doesn't wrap per-word.
_BUBBLE_WIDTH_RATIO_NARROW = 0.62
_BUBBLE_WIDTH_RATIO_WIDE = 0.94
_BUBBLE_WIDTH_MIN = 40
_BUBBLE_WIDTH_MAX_NARROW = 110
_BUBBLE_WIDTH_MAX_WIDE = 200
_NARROW_THRESHOLD = 60


def _bubble_width(console: Console) -> int:
    w = console.width
    if w < _NARROW_THRESHOLD:
        ratio = _BUBBLE_WIDTH_RATIO_NARROW
        cap = _BUBBLE_WIDTH_MAX_NARROW
    else:
        ratio = _BUBBLE_WIDTH_RATIO_WIDE
        cap = _BUBBLE_WIDTH_MAX_WIDE
    return max(_BUBBLE_WIDTH_MIN, min(cap, int(w * ratio)))


# ---------------------------------------------------------------------------
# Static bubbles — minimal color-bar style (no full border).
#
# Layout for each turn:
#
#     <label>                       ← dim, e.g. "● deepseek-v4-pro"
#     ▎ message body                ← thin colored bar on the left, no border
#
# User messages are right-aligned; assistant/tool are left-aligned. The color
# of the bar + label distinguishes who is talking (green=user, cyan=assistant,
# magenta=tool) — same color convention as the old bordered panels, so muscle
# memory transfers.
# ---------------------------------------------------------------------------

#: The left color bar glyph. A thin vertical bar reads cleaner than a full
#: box border at full terminal width and avoids the CJK width-miscount issues
#: that plagued the bordered Panel.
_BAR = "▎"


def render_user_bubble(console: Console, text: str) -> None:
    """Print the user's message — right-aligned, green color bar, no border."""
    body = Text.assemble((_BAR, "green"), (" " + text, ""))
    console.print(Align(body, align="right"))


def render_tool_bubble(
    console: Console,
    *,
    tool_name: str,
    args_preview: str,
    body: str,
) -> None:
    """Print a tool call/result — left-aligned, magenta color bar, no border."""
    # Label line: tool name + dimmed args.
    label = Text.assemble(
        ("▸ ", "magenta"),
        (tool_name, "bold magenta"),
    )
    if args_preview:
        label.append(f"  {args_preview}", style="dim")
    capped = body if len(body) <= 1200 else body[:1200] + "\n…"
    content = Text.assemble((_BAR, "magenta"), (" " + capped, ""))
    console.print(label)
    console.print(content)


# ---------------------------------------------------------------------------
# Streaming assistant output
# ---------------------------------------------------------------------------

def _tail_lines(text: str, n: int) -> str:
    """Last ``n`` source lines of ``text``, with a leading ellipsis when trimmed."""
    if not text:
        return text
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "…\n" + "\n".join(lines[-n:])


class AssistantStream:
    """Stream the assistant turn into a cyan-bordered Panel.

    Layout (one Panel titled ``● <model_name>``)::

        ╭─ ● deepseek-v4-pro ─────────────────────────────────────────╮
        │ // thinking                                                  │  ← dim italic
        │ 用户问了什么，我应该如何回答 …                                │  ← dim italic
        │ ──────                                                       │
        │ # 回答正文                                                   │
        │ - Markdown 渲染过的内容 (粗体、列表、代码块)                 │
        ╰──────────────────────────────────────────────────────────────╯

    Why this version (finally) survives long output
    -----------------------------------------------
    Two earlier iterations failed:

    * v1: ``Live(transient=False, vertical_overflow="visible")`` — Live's
      cursor-up redraw broke once content scrolled off-screen, every
      refresh stacked a fresh panel header. Reliably reproducible on
      Windows Terminal at ~15 visible rows.
    * v2: raw ``console.file.write`` — robust but lost both the panel
      border *and* Markdown rendering, which the user wanted back.

    * v3: ``vertical_overflow="crop"`` + ``transient=True`` — better, but
      crop fills the FULL screen height with zero margin. Ambiguous-width
      chars (¥ … ：) render 2 cells wide on Windows Terminal while Rich
      measures 1, so a full-height frame still wraps past the screen and
      the stacked-header bug came back on CJK-heavy answers.

    This v4 keeps Live but renders a **tail view** while streaming:

    1. ``_render(final=False)`` trims the body to the last N source lines
       (terminal height minus a generous margin). The live region is
       always well under the screen height, so wide-char wrap mis-measure
       is absorbed by the margin and ``cursor up N`` stays reachable.
    2. ``transient=True`` so when the stream ends, Live clears its small
       region cleanly. No full-frame redraw right before exit — clearing
       the tallest frame is exactly when miscounted rows leave remnants.
    3. On ``__exit__`` we ``console.print(self._render(final=True))`` ONCE
       to deposit the **full** panel into scrollback. Live-time view is a
       progress monitor; the post-exit print is the canonical record.

    During the stream you only see the *tail* of a long answer (thinking
    collapses to its last lines once the answer starts). The full content
    is always present in the final post-exit panel.

    ``show_reasoning_inline`` (default ``True``) toggles whether the
    thinking section is rendered inside the panel. ``reasoning_content``
    is **always** captured on :attr:`reasoning` for the session log so
    MiMo / Kimi-thinking multi-turn invariants aren't broken.
    """

    def __init__(
        self,
        console: Console,
        *,
        model_name: str,
        show_reasoning_inline: bool = True,
        show_full: bool = False,
        collapse_threshold: int = 800,
        refresh_per_second: int = 8,
    ) -> None:
        self.console = console
        self.model_name = model_name
        self.show_reasoning_inline = show_reasoning_inline
        # When show_full is True, reasoning_content is always rendered
        # in full (Ctrl+O toggled this on). Otherwise, the post-exit
        # render collapses content longer than ``collapse_threshold`` chars
        # to a preview + "Ctrl+O to expand" hint.
        self.show_full = show_full
        self.collapse_threshold = collapse_threshold
        self.refresh_per_second = refresh_per_second
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._live: Live | None = None
        self._opened = False

    # ------------------------------------------------------------------

    def __enter__(self) -> "AssistantStream":
        try:
            self._live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=self.refresh_per_second,
                transient=True,                  # clear on exit; we'll print the final panel ourselves
                vertical_overflow="crop",        # **critical**: never exceed viewport
            )
            self._live.__enter__()
        except Exception:
            self._live = None
        self._opened = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._opened:
            return
        # Close Live → clears its region. NOTE: no update() before exit — the
        # final frame is the tallest one, and redrawing it right before the
        # transient clear maximizes the damage if the clear miscounts rows
        # (ambiguous-width CJK chars render wider than Rich measures on
        # Windows Terminal, so tall frames are exactly the risky ones).
        if self._live is not None:
            try:
                self._live.__exit__(exc_type, exc, tb)
            except Exception:
                pass
            self._live = None
        # Deposit the FULL (uncropped) panel into scrollback. This is what
        # the user reads after the response is done. Wrapping in try/except
        # so a Markdown parse glitch on the last token can't crash the REPL.
        try:
            self.console.print(self._render(final=True))
        except Exception:
            # Last-resort fallback so the user at least sees text content.
            try:
                self.console.print(f"[bold cyan]● {self.model_name}[/bold cyan]")
                if self._reasoning_parts and self.show_reasoning_inline:
                    self.console.print(Text(self.reasoning, style="dim italic"))
                self.console.print(self.content or "[dim](no content)[/dim]")
            except Exception:
                pass
        self._opened = False

    # ------------------------------------------------------------------
    # Delta methods (called by the agent loop)
    # ------------------------------------------------------------------

    def append_content(self, text: str) -> None:
        if not text:
            return
        self._content_parts.append(text)
        if self._live is not None:
            try:
                self._live.update(self._render())
            except Exception:
                # If Live falls over mid-stream, drop to raw passthrough
                # for the rest of this turn so the user still sees tokens.
                self._live = None

    def append_reasoning(self, text: str) -> None:
        if not text:
            return
        self._reasoning_parts.append(text)
        if self._live is not None and self.show_reasoning_inline:
            try:
                self._live.update(self._render())
            except Exception:
                self._live = None

    @property
    def content(self) -> str:
        return "".join(self._content_parts)

    @property
    def reasoning(self) -> str:
        return "".join(self._reasoning_parts)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self, *, final: bool = False):
        """Build the renderable — minimal color-bar style, no full border.

        ``final=False`` (streaming) renders a **tail view**: the body is
        trimmed to the last N source lines so the live region stays well under
        the terminal height. ``final=True`` renders everything (the canonical
        post-exit record in scrollback).

        Layout::

            ● deepseek-v4-pro            ← cyan label (no border)
            ▎ // thinking                ← dim italic, under the bar
            ▎ <reasoning tail>
            ▎ ────                       ← separator
            ▎ <markdown content>

        Replacing the bordered Panel with a color bar fixes the long-standing
        CJK width-miscount issue: there's no full-height frame for
        wide-char wrap to overflow, so Live's cursor-up redraw stays stable.
        """
        live_view = not final
        reasoning_text = self.reasoning
        content_text = self.content

        if live_view:
            # Budget in source lines, with generous margin below the screen
            # height to absorb wide-char wrapping Rich can't see.
            try:
                screen_h = self.console.size.height
            except Exception:
                screen_h = 24
            budget = max(6, screen_h - 8)
            if content_text:
                # Once the answer starts, collapse thinking to its last lines.
                reasoning_text = _tail_lines(reasoning_text, 3)
                content_text = _tail_lines(content_text, max(4, budget - 5))
            else:
                reasoning_text = _tail_lines(reasoning_text, budget)

        items: list = []

        # --- Label line (cyan, replaces the old panel title) -------------
        items.append(Text(f"● {self.model_name}", style="bold cyan"))

        # --- Thinking block (dim italic, indented under the bar) ---------
        if self.show_reasoning_inline and reasoning_text:
            # Post-exit view: collapse long reasoning unless Ctrl+O is on.
            # Live view already uses tail_lines so the threshold is a no-op
            # during streaming — only the final render feels the difference.
            if (
                live_view is False
                and not self.show_full
                and self.collapse_threshold > 0
                and len(reasoning_text) > self.collapse_threshold
            ):
                preview_chars = min(200, self.collapse_threshold)
                preview = reasoning_text[:preview_chars].rstrip()
                if len(preview) < len(reasoning_text):
                    preview = preview.rstrip() + "…"
                items.append(Text(f"{_BAR} // thinking", style="bold dim"))
                items.append(Text(f"{_BAR} {preview}", style="dim italic"))
                hidden = len(reasoning_text) - len(preview)
                items.append(Text(
                    f"{_BAR} … 折叠了 {hidden} 字符 (Ctrl+O 全显 — 当前阈值 {self.collapse_threshold} 字符)",
                    style="dim italic",
                ))
            else:
                # Plain dim italic Text — NOT Markdown. Thinking traces from
                # reasoning models often contain stray ``*`` / ``_`` that
                # would accidentally bold-toggle if parsed.
                items.append(Text(f"{_BAR} // thinking", style="bold dim"))
                for rline in reasoning_text.splitlines() or [""]:
                    items.append(Text(f"{_BAR} {rline}", style="dim italic"))
            if content_text:
                items.append(Text(f"{_BAR} ────", style="dim"))

        # --- Content block (Markdown, indented to sit under the bar) ------
        # The bar is 1 cell + 1 space = 2 cells. We can't prefix inside
        # Markdown (it would break code fences / lists), so we indent the
        # whole Markdown block by 2 via Padding. This keeps the visual bar
        # metaphor consistent with the thinking block above.
        if content_text:
            try:
                md = Markdown(content_text, code_theme="monokai")
                items.append(Padding(md, (0, 0, 0, 2)))
            except Exception:
                # Fallback: plain text with bar prefix per line.
                for cline in content_text.splitlines() or [""]:
                    items.append(Text(f"{_BAR} {cline}"))
        elif not reasoning_text:
            items.append(Text(f"{_BAR} …", style="dim"))

        return Group(*items)


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------

@dataclass
class TurnStats:
    used_tokens: int
    context_window: int
    reasoning_tokens: int
    last_response_chars: int
    last_response_ms: int
    iterations: int

    @property
    def used_pct(self) -> float:
        return (self.used_tokens / self.context_window) * 100 if self.context_window else 0.0

    @property
    def free_tokens(self) -> int:
        return max(0, self.context_window - self.used_tokens)


def compute_turn_stats(
    *,
    entry: ModelEntry,
    messages: Iterable[ChatMessage],
    last_response: ChatResponse | None,
    iterations: int,
) -> TurnStats:
    used = estimate_session_tokens(messages)
    window = context_window_for(entry)
    reasoning_tokens = estimate_reasoning_tokens(messages)
    last_chars = len(last_response.content or "") if last_response else 0
    last_ms = last_response.elapsed_ms if last_response else 0
    return TurnStats(
        used_tokens=used,
        context_window=window,
        reasoning_tokens=reasoning_tokens,
        last_response_chars=last_chars,
        last_response_ms=last_ms,
        iterations=iterations,
    )


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def status_bar_text(stats: TurnStats, *, model_name: str) -> Text:
    """Build the status-bar single-line as a Rich :class:`Text` object.

    Single-line with width control / truncation so it can be printed
    under streaming bubbles without breaking the layout.
    """
    pct = stats.used_pct
    bar_color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append("model ", style="dim")
    t.append(model_name)
    t.append("   tokens ", style="dim")
    t.append(
        f"{_fmt_tokens(stats.used_tokens)}/{_fmt_tokens(stats.context_window)}",
        style=bar_color,
    )
    t.append(f" ({pct:.1f}% used · {_fmt_tokens(stats.free_tokens)} free)")
    if stats.reasoning_tokens:
        t.append("   reasoning_content ", style="dim")
        t.append(f"~{_fmt_tokens(stats.reasoning_tokens)} t")
    if stats.last_response_ms:
        t.append("   turn ", style="dim")
        t.append(f"{stats.last_response_ms} ms · {stats.iterations} iter")
    return t


# ---------------------------------------------------------------------------
# Multi-line context panel (full width, always on)
# ---------------------------------------------------------------------------

#: Width (in cells) at or below which the panel collapses to the compact form.
_PANEL_NARROW_THRESHOLD = 70


def _progress_bar(pct: float, width: int = 20) -> tuple[str, str]:
    """Return ``(filled_bar, color)`` for an ASCII progress bar.

    ``filled`` is a string of ``width`` cells: ``█`` for filled, ``░`` for empty.
    ``color`` follows the green/yellow/red convention used elsewhere.
    """
    width = max(8, width)
    filled = max(0, min(width, round(width * pct / 100)))
    bar = "█" * filled + "░" * (width - filled)
    color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
    return bar, color


def render_context_panel(
    console: Console,
    stats: TurnStats,
    *,
    model_name: str,
    role_breakdown: dict[str, int] | None = None,
    todo_summary: dict[str, int] | None = None,
    current_todo: str | None = None,
) -> None:
    """Print the multi-line context info panel after each turn.

    Full width, always on. Collapses to a compact two-segment form on narrow
    terminals (< 70 cols). Falls back to the legacy single-line
    :func:`status_bar_text` on any rendering error so the REPL never breaks.

    Layout (wide, ≥ 70 cols)::

        ─ context ──── <hline to console.width> ────
          [██░░░░░░░░░░░░░░░░░░] 12%  12.3k / 1.0M  剩余 987.7k
          前缀 3.1k · 推理 2.1k · 工具 4.8k · 对话 2.3k
          计划 ▸ 2/5  进行中:优化气泡宽度   耗时 3420ms · 1 轮
    """
    try:
        width = console.width
        compact = width < _PANEL_NARROW_THRESHOLD
        pct = stats.used_pct

        # --- line 1: title separator across the full width -----------------
        title = " ctx " if compact else " context "
        title_cell_w = len(title)  # ASCII-only title, cell count == char count
        remaining = max(0, width - title_cell_w)
        # Split the dashes so the title sits roughly 1/4 from the left edge,
        # matching the spec mockup ("─ context ───────…").
        left_dash = min(remaining, 2)
        right_dash = remaining - left_dash
        console.print(
            Text("─" * left_dash + title + "─" * right_dash, style="dim")
        )

        # --- line 2: progress bar + totals ---------------------------------
        bar_cells = 12 if compact else 20
        bar, color = _progress_bar(pct, width=bar_cells)
        line2 = Text(no_wrap=True, overflow="ellipsis")
        line2.append("[", style="dim")
        line2.append(bar, style=color)
        line2.append("] ", style="dim")
        line2.append(f"{pct:.0f}% ", style=color)
        line2.append(
            f"{_fmt_tokens(stats.used_tokens)}/{_fmt_tokens(stats.context_window)}",
        )
        if not compact:
            line2.append(f"  剩余 {_fmt_tokens(stats.free_tokens)}", style="dim")
        console.print(line2)

        # --- line 3: role breakdown ----------------------------------------
        rb = role_breakdown or {"prefix": 0, "reasoning": 0, "tool": 0, "conversation": 0}
        line3 = Text(no_wrap=True, overflow="ellipsis")
        if compact:
            line3.append(
                f"前缀{_fmt_tokens(rb.get('prefix', 0))} "
                f"推理{_fmt_tokens(rb.get('reasoning', 0))} "
                f"工具{_fmt_tokens(rb.get('tool', 0))} "
                f"对话{_fmt_tokens(rb.get('conversation', 0))}",
                style="dim",
            )
        else:
            line3.append("前缀 ", style="dim")
            line3.append(_fmt_tokens(rb.get("prefix", 0)))
            line3.append(" · 推理 ", style="dim")
            line3.append(_fmt_tokens(rb.get("reasoning", 0)))
            line3.append(" · 工具 ", style="dim")
            line3.append(_fmt_tokens(rb.get("tool", 0)))
            line3.append(" · 对话 ", style="dim")
            line3.append(_fmt_tokens(rb.get("conversation", 0)))
        console.print(line3)

        # --- line 4 (optional): todo progress + timing ---------------------
        # Spacing convention: Chinese label + space + value, sections joined
        # by " · ". This keeps CJK/ASCII mixing visually balanced.
        has_todo = bool(todo_summary and todo_summary.get("total", 0) > 0)
        if has_todo or stats.last_response_ms:
            line4 = Text(no_wrap=True, overflow="ellipsis")
            sections: list[Text] = []
            if has_todo:
                done = todo_summary.get("done", 0)  # type: ignore[union-attr]
                total = todo_summary.get("total", 0)  # type: ignore[union-attr]
                in_prog = todo_summary.get("in_progress", 0)  # type: ignore[union-attr]
                todo_txt = Text()
                todo_txt.append("计划 ", style="dim")
                todo_txt.append(f"{done}/{total}", style="bold cyan")
                if current_todo:
                    label = current_todo
                    if len(label) > 20:
                        label = label[:20] + "…"
                    todo_txt.append("  ▸ ", style="cyan")
                    todo_txt.append(label, style="cyan")
                elif in_prog:
                    todo_txt.append(f"  ({in_prog} 进行中)", style="dim cyan")
                sections.append(todo_txt)
            if stats.last_response_ms:
                time_txt = Text()
                time_txt.append(f"{stats.last_response_ms}ms", style="dim")
                if not compact:
                    time_txt.append(f" · {stats.iterations} 轮", style="dim")
                sections.append(time_txt)
            # Join sections with " · " separator.
            for i, sec in enumerate(sections):
                if i > 0:
                    line4.append(" · ", style="dim")
                line4.append_text(sec)
            console.print(line4)
    except Exception:
        # Fallback: never let the info panel break the REPL.
        try:
            console.print(status_bar_text(stats, model_name=model_name))
        except Exception:
            pass


def render_reasoning_meter(
    console: Console,
    *,
    reasoning_text: str,
    inline: bool = False,
) -> None:
    """Print a one-liner like ``reasoning_content: 182 字符 (~46 t)``."""
    chars = len(reasoning_text)
    if chars == 0:
        return
    toks = estimate_tokens(reasoning_text)
    style = "dim italic" if inline else "dim"
    console.print(
        f"[{style}]reasoning_content: {chars} 字符 (~{toks} tokens) — 已保留到会话历史[/{style}]"
    )


__all__ = [
    "AssistantStream",
    "TurnStats",
    "compute_turn_stats",
    "render_user_bubble",
    "render_tool_bubble",
    "status_bar_text",
    "render_context_panel",
    "render_reasoning_meter",
]
