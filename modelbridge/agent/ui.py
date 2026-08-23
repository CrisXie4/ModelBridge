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
from typing import Callable, Iterable

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel as RichPanel
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


# ---------------------------------------------------------------------------
# Right-side status panel (opencode style — cwd / ctx / cache / todos / bg / tools)
# ---------------------------------------------------------------------------

class SidePanel:
    """Mutable state behind the right-side status panel.

    The REPL's callbacks feed it (tool calls, cache outcomes, todos come from
    the store, background tasks from the manager); :meth:`render` snapshots it
    into a fixed-width panel. Updated live during each turn (the streaming
    bubble carries it as a right column) and printed once after the turn.
    """

    def __init__(self, *, cwd: str, todo_store=None, bg_manager=None) -> None:
        self.cwd = cwd
        self.todo_store = todo_store
        self.bg_manager = bg_manager
        self.model_name: str = ""
        self.ctx_line: str = ""            # "4% 35.0k/1.0M"
        self.cache_last: str = ""          # "27% 12.3k/45.6k"
        self.cache_session = ""            # "45% 89/197 (轮)"
        self.tools: list[tuple[str, str, bool]] = []   # (name, preview, is_error)
        self._cache_hits = 0
        self._cache_misses = 0

    # -- feeders (called from REPL callbacks) -------------------------------

    def begin_turn(self, model_name: str) -> None:
        self.tools = []
        self.model_name = model_name

    def add_tool(self, name: str, preview: str, is_error: bool) -> None:
        self.tools.append((name, preview, is_error))
        if len(self.tools) > 30:
            self.tools = self.tools[-30:]

    def update_ctx(self, *, used_pct: float, used_tokens: int, window: int) -> None:
        self.ctx_line = f"{used_pct:.0f}% {_fmt_tokens(used_tokens)}/{_fmt_tokens(window)}"

    def update_cache(self, hit: int, miss: int) -> None:
        total = hit + miss
        if total > 0:
            self.cache_last = f"{hit * 100 // total}% ({_fmt_tokens(hit)})"
        self._cache_hits += hit
        self._cache_misses += miss
        s_total = self._cache_hits + self._cache_misses
        if s_total > 0:
            self.cache_session = (
                f"{self._cache_hits * 100 // s_total}% ({_fmt_tokens(s_total)})"
            )

    # -- rendering -----------------------------------------------------------

    def status_items(self) -> list[tuple[str, str]]:
        """(style, text) segments for the persistent BOTTOM status bar.

        Plain text only (no emoji) — each segment is self-contained so the
        bottom bar can join them with " · " and the TUI rail can stack them.
        """
        segs: list[tuple[str, str]] = []
        cwd_disp = self.cwd
        if len(cwd_disp) > 32:
            cwd_disp = "…" + cwd_disp[-31:]
        segs.append(("#8a8f98", f"目录 {cwd_disp}"))
        if self.model_name:
            pct_val = 0.0
            if self.ctx_line:
                try:
                    pct_val = float(self.ctx_line.split("%", 1)[0])
                except ValueError:
                    pass
            ctx_color = "#3fb950" if pct_val < 60 else ("#d29922" if pct_val < 85 else "#f85149")
            segs.append(("#58a6ff bold", f"模型 {self.model_name}"))
            if self.ctx_line:
                segs.append((ctx_color, self.ctx_line))
        if self.cache_last:
            segs.append(("#d29922", f"缓存 {self.cache_last}"))
            if self.cache_session:
                segs.append(("#8a8f98", f"会话 {self.cache_session}"))
        todos = self._todos()
        if todos:
            done = sum(1 for m, _c, s in todos if s == "green")
            segs.append(("#58a6ff", f"计划 {done}/{len(todos)}"))
        bgs = self._bg_tasks()
        if bgs:
            running = sum(1 for m, _t, s in bgs if s == "cyan")
            failed = sum(1 for m, _t, s in bgs if s == "red")
            segs.append(
                ("#f85149" if failed and not running else "#58a6ff", f"后台 {running}")
            )
        return segs

    def render_rail(self, console: Console) -> RenderableType | None:
        """The RAIL-only panel: todos / background tasks / this turn's tools.

        The status trio (cwd / model+ctx / cache) lives in the persistent
        BOTTOM status bar (see :meth:`status_items`), so the after-turn
        transcript print carries only the task rail — matching opencode's
        split. Returns None when the rail is empty (nothing to print).
        """
        width = max(30, min(48, console.width // 3))
        try:
            max_lines = max(8, console.size.height - 6)
        except Exception:
            max_lines = 16

        lines: list[Text] = []
        todos = self._todos()
        if todos:
            lines.append(Text("任务", style="bold"))
            shown = todos[: max(4, (max_lines - len(lines) - 6))]
            for mark, content, style in shown:
                lines.append(Text.assemble((f"[{mark}] ", style), (content, "")))
            hidden = len(todos) - len(shown)
            if hidden > 0:
                lines.append(Text(f"… 还有 {hidden} 项", style="dim"))

        bgs = self._bg_tasks()
        if bgs:
            lines.append(Text("后台", style="bold"))
            for mark, txt, style in bgs[:6]:
                lines.append(Text.assemble((f"{mark} ", style), (txt, "")))

        if self.tools:
            lines.append(Text("本轮", style="bold"))
            for name, preview, is_error in self.tools[-8:]:
                mark = "!" if is_error else "+"
                style = "red" if is_error else "green"
                label = f"{name} {preview}".strip()
                if len(label) > width - 6:
                    label = label[: width - 7] + "…"
                lines.append(Text.assemble((mark + " ", style), (label, "")))
            if len(self.tools) > 8:
                lines.append(Text(f"… 共 {len(self.tools)} 次调用", style="dim"))

        if not lines:
            return None
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(Text("…", style="dim"))
        return RichPanel(
            Group(*lines),
            title="活动",
            border_style="dim",
            width=width,
            box=box.ROUNDED,
        )

    def render(self, console: Console) -> RenderableType:
        """Snapshot the current state into a fixed-width panel (~1/3 screen)."""
        width = max(30, min(48, console.width // 3))
        # Height budget: never let the panel exceed the live region's budget.
        try:
            max_lines = max(8, console.size.height - 6)
        except Exception:
            max_lines = 16

        lines: list[Text] = []

        cwd_disp = self.cwd
        if len(cwd_disp) > width - 8:
            cwd_disp = "…" + cwd_disp[-(width - 9):]
        lines.append(Text.assemble(("目录  ", "dim"), (cwd_disp, "")))

        if self.model_name:
            ctx = f"  {self.ctx_line}" if self.ctx_line else ""
            lines.append(Text.assemble(("模型  ", "dim"), (self.model_name, "bold cyan"), (ctx, "dim")))
        if self.cache_last:
            lines.append(Text.assemble(
                ("缓存  ", "dim"),
                (self.cache_last, "yellow" if self.cache_last.startswith(("0%", "1", "2", "3")) else "green"),
                (f"  会话 {self.cache_session}", "dim"),
            ))
        if len(self.cache_last) + len(self.cache_session) > width - 10:
            # 窄面板：缓存行拆两行，避免折行错位。
            lines = lines[:-1] + [
                Text.assemble(("缓存  ", "dim"), (self.cache_last, "green")),
                Text.assemble(("      会话 ", "dim"), (self.cache_session, "dim")),
            ]

        # Todos as checkboxes — the panel's centrepiece.
        todos = self._todos()
        if todos:
            lines.append(Text("任务", style="bold"))
            shown = todos[: max(4, (max_lines - len(lines) - 6))]
            for mark, content, style in shown:
                lines.append(Text.assemble((f"[{mark}] ", style), (content, "")))
            hidden = len(todos) - len(shown)
            if hidden > 0:
                lines.append(Text(f"… 还有 {hidden} 项", style="dim"))

        # Background tasks.
        bgs = self._bg_tasks()
        if bgs:
            lines.append(Text("后台", style="bold"))
            for mark, txt, style in bgs[:6]:
                lines.append(Text.assemble((f"{mark} ", style), (txt, "")))

        # This turn's tool activity.
        if self.tools:
            lines.append(Text("本轮", style="bold"))
            for name, preview, is_error in self.tools[-8:]:
                mark = "!" if is_error else "+"
                style = "red" if is_error else "green"
                label = f"{name} {preview}".strip()
                if len(label) > width - 6:
                    label = label[: width - 7] + "…"
                lines.append(Text.assemble((mark + " ", style), (label, "")))
            if len(self.tools) > 8:
                lines.append(Text(f"… 共 {len(self.tools)} 次调用", style="dim"))

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(Text("…", style="dim"))
        return RichPanel(
            Group(*lines) if lines else Text("(空)", style="dim"),
            title="状态",
            border_style="dim",
            width=width,
            box=box.ROUNDED,
        )

    def _todos(self) -> list[tuple[str, str, str]]:
        if self.todo_store is None:
            return []
        try:
            items = self.todo_store.to_list()
        except Exception:
            return []
        out = []
        for it in items:
            if it.get("status") == "done":
                out.append(("x", it.get("content", ""), "green"))
            elif it.get("status") == "in_progress":
                out.append(("~", it.get("content", ""), "cyan"))
            else:
                out.append((" ", it.get("content", ""), "dim"))
        return out

    def _bg_tasks(self) -> list[tuple[str, str, str]]:
        if self.bg_manager is None:
            return []
        try:
            snaps = self.bg_manager.snapshot_for_panel()
        except Exception:
            return []
        out = []
        for t in snaps:
            cmd = t["command"]
            if len(cmd) > 24:
                cmd = cmd[:24] + "…"
            if t["status"] == "running":
                out.append((">", f"{t['id']} {t['duration_s']}s {cmd}", "cyan"))
            elif t["status"] == "done":
                out.append(("+", f"{t['id']} {t['duration_s']}s {cmd}", "green"))
            else:
                out.append(("!", f"{t['id']} {t['duration_s']}s {cmd}", "red"))
        return out

    def render_provider(self, console: Console) -> Callable[[], RenderableType]:
        """Zero-arg callable for AssistantStream's live right column."""
        return lambda: self.render(console)


def render_side_panel(console: Console, panel: SidePanel) -> None:
    """Print the task rail into the transcript (after each turn).

    Rail-only (任务/后台/本轮): the status trio lives in the persistent
    bottom status bar, so it is NOT duplicated here. Empty rail → no output
    (the bottom bar already tells the story).
    """
    try:
        rail = panel.render_rail(console)
        if rail is not None:
            console.print(rail)
    except Exception:
        pass  # the panel must never break the REPL


def render_user_bubble(console: Console, text: str) -> None:
    """Print the user's message — right-aligned, green color bar, no border."""
    body = Text.assemble((_BAR, "green"), (" " + text, ""))
    console.print(Align(body, align="right"))


#: Tool bodies at or below this length stay visible as a second dim line;
#: anything longer collapses to a size hint (the model still gets the full
#: body — it lives in the session; this is display-side only).
_TOOL_BODY_INLINE_CHARS = 160


def render_tool_bubble(
    console: Console,
    *,
    tool_name: str,
    args_preview: str,
    body: str,
    is_error: bool = False,
) -> None:
    """Print a compact right-aligned tool-call entry (opencode style).

    One line per call — ``✓ tool args · size`` — keeps the transcript
    readable when a turn makes dozens of calls; a short body rides inline,
    an error shows its first line. The full body always reaches the model
    (it lives in the session history), so display-side folding loses
    nothing the user can't re-ask for.
    """
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append("✗ " if is_error else "✓ ", style="red" if is_error else "green")
    line.append(tool_name, style="bold magenta")
    if args_preview:
        line.append(f" {args_preview}", style="dim")
    body = (body or "").strip()
    if body:
        if is_error:
            first = body.splitlines()[0][:100]
            line.append(f" — {first}", style="red")
        elif len(body) <= _TOOL_BODY_INLINE_CHARS:
            one = body.replace("\n", " ⏎ ")
            line.append(f" · {one}", style="dim")
        else:
            line.append(f" · {len(body)} 字符", style="dim")
    console.print(Align(line, align="right"))


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
        side_panel: Callable[[], RenderableType] | None = None,
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
        # Live right column (the SidePanel): during streaming the live region
        # becomes a two-column layout — conversation tail left, status panel
        # right (~1/3 width). None = plain single-column streaming.
        self.side_panel = side_panel
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
        # Pure tool-call iteration: the model said nothing on screen (the
        # live `▎ …` placeholder was transient). Deposit nothing — otherwise
        # every tool round leaves an empty `● model / ▎ …` stub in the
        # transcript and the display fills with header noise.
        if not self._content_parts and not self._reasoning_parts:
            self._opened = False
            return
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

        body = Group(*items)
        if live_view and self.side_panel is not None:
            # Two-column live view: conversation tail left, status panel
            # right (~1/3). The final (post-exit) deposit stays single-column;
            # the panel is printed separately by the turn-end hook.
            try:
                from rich.layout import Layout

                layout = Layout()
                side = Layout(self.side_panel(), name="side")
                # rich 运行时支持最小宽度，但类型标注没跟上 — setattr 绕过。
                setattr(side, "minimum", 30)
                layout.split_row(Layout(body, name="left"), side)
                return layout
            except Exception:
                pass
        return body


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
# End-of-turn context line (right-aligned, one line — opencode style)
# ---------------------------------------------------------------------------


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
    """Print the one-line context summary after each turn — right-aligned.

    Replaced the former full-width multi-line panel: the transcript stays
    clean and the status hugs the right edge above the next prompt, the way
    opencode's status line reads. Everything the old panel showed is still
    here, just packed: model, context usage, the two volatile role buckets
    (reasoning / tool), todo progress, and turn timing. ``/context`` prints
    the detailed multi-line breakdown on demand. Falls back to the legacy
    :func:`status_bar_text` on any rendering error so the REPL never breaks.

    Layout::

        deepseek-v4-pro  ctx 12% 12.3k/1.0M · 推理2.1k 工具4.8k · 计划 2/5 ▸修复登录 · 3420ms
    """
    try:
        pct = stats.used_pct
        color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(model_name, style="bold cyan")
        line.append("  ctx ", style="dim")
        line.append(f"{pct:.0f}%", style=color)
        line.append(
            f" {_fmt_tokens(stats.used_tokens)}/{_fmt_tokens(stats.context_window)}",
            style="dim",
        )
        if role_breakdown:
            line.append(
                " · "
                f"推理{_fmt_tokens(role_breakdown.get('reasoning', 0))} "
                f"工具{_fmt_tokens(role_breakdown.get('tool', 0))}",
                style="dim",
            )
        if todo_summary and todo_summary.get("total", 0) > 0:
            done = todo_summary.get("done", 0)
            total = todo_summary.get("total", 0)
            line.append(" · 计划 ", style="dim")
            line.append(f"{done}/{total}", style="cyan")
            if current_todo:
                label = current_todo if len(current_todo) <= 16 else current_todo[:16] + "…"
                line.append(f" ▸{label}", style="cyan")
        if stats.last_response_ms:
            line.append(f" · {stats.last_response_ms}ms", style="dim")
        console.print(Align(line, align="right"))
    except Exception:
        # Fallback: never let the info line break the REPL.
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
    "SidePanel",
    "TurnStats",
    "compute_turn_stats",
    "render_user_bubble",
    "render_tool_bubble",
    "render_side_panel",
    "status_bar_text",
    "render_context_panel",
    "render_reasoning_meter",
]
