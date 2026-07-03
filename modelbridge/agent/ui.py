"""Chat-bubble REPL UI helpers.

* :func:`render_user_bubble`     — user input in a right-aligned panel.
* :func:`render_tool_bubble`     — tool call/result on the left.
* :func:`status_bar_text`        — build the status-line as a Text object.
* :func:`render_status_bar`      — print the status line normally.
* :class:`AssistantStream`       — context manager that prints a left
  panel and refreshes it live as content/reasoning deltas arrive.
* :class:`StickyFooter`          — context manager that pins one
  rich-rendered line to the **bottom of the terminal** via the
  ANSI scroll region (DECSTBM). Normal output scrolls above it.

Everything here is rich-only — no I/O or model calls. Easy to unit-test
and easy to swap for a future web UI.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from io import StringIO
from typing import Iterable

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
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

#: Bubble takes up ~60% of console width, leaving 40% margin on the opposite side.
_BUBBLE_WIDTH_RATIO = 0.62
_BUBBLE_WIDTH_MIN = 40
_BUBBLE_WIDTH_MAX = 110


def _bubble_width(console: Console) -> int:
    w = int(console.width * _BUBBLE_WIDTH_RATIO)
    return max(_BUBBLE_WIDTH_MIN, min(_BUBBLE_WIDTH_MAX, w))


# ---------------------------------------------------------------------------
# Static bubbles
# ---------------------------------------------------------------------------

def render_user_bubble(console: Console, text: str) -> None:
    """Print the user's message as a right-aligned green panel."""
    panel = Panel(
        Text(text, no_wrap=False),
        title="[bold green]you[/bold green]",
        title_align="right",
        border_style="green",
        width=_bubble_width(console),
        padding=(0, 1),
    )
    console.print(Align(panel, align="right"))
    # After printing, the cursor is on a new line inside the scroll region.
    # Move it to row H so the sticky footer (Prompt.ask input row) stays clean.
    try:
        h = console.size.height
        sys.stdout.write(f"\x1b[{h};1H")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 — cursor escape is best-effort
        pass


def render_tool_bubble(
    console: Console,
    *,
    tool_name: str,
    args_preview: str,
    body: str,
) -> None:
    """Print a tool call/result on the left, magenta border."""
    title = f"[bold magenta]tool · {tool_name}[/bold magenta]"
    if args_preview:
        title += f"  [dim]({args_preview})[/dim]"
    capped = body if len(body) <= 1200 else body[:1200] + "\n…"
    panel = Panel(
        Text(capped, no_wrap=False),
        title=title,
        title_align="left",
        border_style="magenta",
        width=_bubble_width(console),
        padding=(0, 1),
    )
    console.print(Align(panel, align="left"))


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
        refresh_per_second: int = 8,
    ) -> None:
        self.console = console
        self.model_name = model_name
        self.show_reasoning_inline = show_reasoning_inline
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
        except Exception:  # noqa: BLE001 — Live setup failure shouldn't kill the turn
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
            except Exception:  # noqa: BLE001
                pass
            self._live = None
        # Deposit the FULL (uncropped) panel into scrollback. This is what
        # the user reads after the response is done. Wrapping in try/except
        # so a Markdown parse glitch on the last token can't crash the REPL.
        try:
            self.console.print(self._render(final=True))
        except Exception:  # noqa: BLE001
            # Last-resort fallback so the user at least sees text content.
            try:
                self.console.print(f"[bold cyan]● {self.model_name}[/bold cyan]")
                if self._reasoning_parts and self.show_reasoning_inline:
                    self.console.print(Text(self.reasoning, style="dim italic"))
                self.console.print(self.content or "[dim](no content)[/dim]")
            except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
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
        """Build the Panel renderable.

        ``final=False`` (streaming) renders a **tail view**: the body is
        trimmed to the last N source lines so the live region stays well under
        the terminal height. This is the v4 fix for stacked panel headers —
        ambiguous-width chars (¥ … ：) render wider on Windows Terminal than
        Rich measures, so a full-height ``crop`` frame can still wrap past the
        screen and break Live's cursor-up redraw. A tail view leaves enough
        margin to absorb the mis-measure. ``final=True`` renders everything
        (the canonical post-exit panel in scrollback).
        """
        live_view = not final
        reasoning_text = self.reasoning
        content_text = self.content

        if live_view:
            # Budget in source lines, with generous margin below the screen
            # height to absorb wide-char wrapping Rich can't see.
            try:
                screen_h = self.console.size.height
            except Exception:  # noqa: BLE001
                screen_h = 24
            budget = max(6, screen_h - 8)
            if content_text:
                # Once the answer starts, collapse thinking to its last lines.
                reasoning_text = _tail_lines(reasoning_text, 3)
                content_text = _tail_lines(content_text, max(4, budget - 5))
            else:
                reasoning_text = _tail_lines(reasoning_text, budget)

        items: list = []

        # --- Thinking block (dim italic) ---------------------------------
        if self.show_reasoning_inline and reasoning_text:
            items.append(Text("// thinking", style="bold dim"))
            # Plain dim italic Text — NOT Markdown. Thinking traces from
            # reasoning models often contain stray ``*`` / ``_`` that
            # would accidentally bold-toggle if parsed.
            items.append(Text(reasoning_text, style="dim italic"))
            if content_text:
                items.append(Rule(style="dim"))

        # --- Content block (Markdown) ------------------------------------
        if content_text:
            try:
                items.append(Markdown(content_text, code_theme="monokai"))
            except Exception:  # noqa: BLE001 — partial markdown edge cases
                items.append(Text(content_text))
        elif not reasoning_text:
            items.append(Text("…", style="dim"))

        body = Group(*items) if len(items) > 1 else (items[0] if items else Text(""))
        return Panel(
            body,
            title=f"[bold cyan]● {self.model_name}[/bold cyan]",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )


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


def render_status_bar(console: Console, stats: TurnStats, *, model_name: str) -> None:
    """Single-line status footer printed after each assistant turn."""
    console.print(status_bar_text(stats, model_name=model_name))


def status_bar_text(stats: TurnStats, *, model_name: str) -> Text:
    """Build the status-bar single-line as a Rich :class:`Text` object.

    Useful for :class:`StickyFooter`, which can't accept multi-line input
    and needs to control width / truncation itself.
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
# Sticky bottom footer (DECSTBM scroll region)
# ---------------------------------------------------------------------------

# Cursor / region escapes. We use the older DECSC / DECRC (ESC 7 / ESC 8)
# pair because the CSI save/restore (CSI s / CSI u) is not implemented by
# every terminal. Windows Terminal handles both fine.
_ESC_SAVE = "\x1b7"
_ESC_RESTORE = "\x1b8"
_ESC_CLEAR_LINE = "\x1b[2K"


def _set_scroll_region(top: int, bottom: int) -> str:
    return f"\x1b[{top};{bottom}r"


def _reset_scroll_region() -> str:
    return "\x1b[r"


def _move_cursor(row: int, col: int = 1) -> str:
    return f"\x1b[{row};{col}H"


class StickyFooter:
    """Pin a single Rich-rendered line to the bottom of the terminal.

    Implementation: set the terminal's DECSTBM scroll region to rows
    ``1..H-1`` while we're active, then write the footer line to row ``H``
    using save/restore cursor so the main scroll region's content stays
    untouched.

    Falls back to a no-op (regular ``console.print`` on each update) when:

    * stdout is not a TTY (e.g. piped to ``head``), OR
    * the terminal is too short to spare a row, OR
    * writing the escape sequence fails.

    Usage::

        with StickyFooter(console) as footer:
            footer.update(some_text)
            console.print("normal output scrolls above")
            footer.update(other_text)
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._active = False
        self._height = 0
        self._width = 0

    # ------------------------------------------------------------------

    def __enter__(self) -> "StickyFooter":
        if not self._supports_tty():
            return self
        self._height = self.console.size.height
        self._width = self.console.size.width
        if self._height < 4:
            return self
        try:
            sys.stdout.write(_set_scroll_region(1, self._height - 1))
            # Place cursor on the last row of the scroll region so the
            # next print starts there instead of jumping back to top.
            sys.stdout.write(_move_cursor(self._height - 1, 1))
            sys.stdout.flush()
        except (OSError, ValueError):
            return self
        self._active = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._active:
            return
        try:
            sys.stdout.write(
                _ESC_SAVE
                + _move_cursor(self._height, 1)
                + _ESC_CLEAR_LINE
                + _ESC_RESTORE
                + _reset_scroll_region()
            )
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        self._active = False

    # ------------------------------------------------------------------

    def update(self, renderable) -> None:
        """Render ``renderable`` (str / Text / etc.) onto the bottom row."""
        if not self._active:
            # Fallback path — print as a regular line.
            self.console.print(renderable)
            return

        # Render the line to ANSI in a side buffer at the current width,
        # truncated to a single line. This avoids the line wrapping and
        # pushing the scroll region content up by mistake.
        buf = StringIO()
        tmp = Console(
            file=buf,
            color_system=self.console.color_system or "truecolor",  # type: ignore[arg-type]
            width=max(20, self._width),
            force_terminal=True,
            soft_wrap=False,
            highlight=False,
            legacy_windows=False,
        )
        tmp.print(renderable, end="", crop=True, no_wrap=True, overflow="ellipsis")
        line = buf.getvalue().rstrip("\r\n")

        try:
            sys.stdout.write(
                _ESC_SAVE
                + _move_cursor(self._height, 1)
                + _ESC_CLEAR_LINE
                + line
                + _ESC_RESTORE
            )
            sys.stdout.flush()
        except (OSError, ValueError):
            # Drop into fallback for the rest of the session.
            self._active = False
            self.console.print(renderable)

    # ------------------------------------------------------------------

    @staticmethod
    def _supports_tty() -> bool:
        try:
            return bool(sys.stdout.isatty())
        except (AttributeError, OSError):
            return False


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
    "StickyFooter",
    "TurnStats",
    "compute_turn_stats",
    "render_user_bubble",
    "render_tool_bubble",
    "render_status_bar",
    "status_bar_text",
    "render_reasoning_meter",
]
