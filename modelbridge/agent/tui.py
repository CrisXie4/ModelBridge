"""Full-screen TUI REPL — opencode-style split layout (2026-08-22).

    ┌──────────────────────────────┬──────────────┐
    │ 对话流（流式/工具/命令输出） │ 状态          │
    │ …                            │  目录 D:\\proj │
    │                              │  模型 ds 4%   │
    │                              │  缓存 16%     │
    │                              │ 任务          │
    │                              │  [x] 收购glm  │
    │                              │ 后台 / 本轮   │
    │ you ❯ 输入行                 │ 审批 y/n/a/o  │
    └──────────────────────────────┴──────────────┘

The prompt_toolkit Application runs on a dedicated UI thread; the agent loop
keeps running on the main thread and feeds it through thread-safe calls
(`write_ansi`, `stream_append`, …). Approvals that used to block on
``Prompt.ask`` go through :class:`ApprovalBroker` — the tool thread waits on
an event while the panel shows the request and y/n/a/o keys answer it.

Everything degrades: if construction/start fails, the caller falls back to
the classic scrolling REPL (``MBRIDGE_CLASSIC=1`` forces it too).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from queue import Queue
from typing import Any, Callable, cast

from .ui import SidePanel
from .ui_prefs import UiPrefs

#: Right rail width bounds when driven by :class:`UiPrefs` — module-level
#: ``rail_width`` keeps the shipped defaults for back-compat/tests.
_MIN_RAIL_PREFS = 24

# ---------------------------------------------------------------------------
# ANSI SGR snippets for the live stream region. The transcript stores raw
# ANSI blocks and prompt_toolkit parses them back into styled fragments, so
# the streaming area can be styled exactly like the classic AssistantStream
# bubble (cyan model label / dim italic thinking / thin separator).
# ---------------------------------------------------------------------------
_A_RESET = "\x1b[0m"
_A_DIM = "\x1b[90m"
_A_MODEL = "\x1b[36m\x1b[1m"          # bold cyan — model label
_A_THINK = "\x1b[2m\x1b[3m"           # dim italic — reasoning deltas
_A_THINK_OFF = "\x1b[23m\x1b[22m"


def rail_width(
    term_width: int,
    *,
    ratio: float = 0.4,
    lo: int = 38,
    hi: int = 72,
) -> int:
    return max(lo, min(hi, int(term_width * ratio)))


# ---------------------------------------------------------------------------
# Transcript — committed blocks + the live streaming region
# ---------------------------------------------------------------------------

def _ansi_to_fragments(block: str) -> list[tuple[str, str]]:
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    try:
        # 片段可能是 (style, text) 或 (style, text, location) 三元组 — 用下标取前两个。
        return [(frag[0] or "", frag[1]) for frag in to_formatted_text(ANSI(block))]
    except Exception:
        return [("", block)]


class TranscriptBuffer:
    """Thread-safe ANSI transcript with a tail view and scroll offset."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blocks: list[str] = []
        self._frag_cache: list[list[tuple[str, str]]] = []
        # Live streaming region (mutable, rendered after the committed part).
        self._stream_parts: list[str] = []
        self.scroll_up = 0  # lines above the tail currently pinned

    def append(self, ansi_block: str) -> None:
        if not ansi_block:
            return
        # NOTE: deliberately does NOT reset scroll_up. While the user is
        # scrolled up reading history (or mid-turn during a long stream),
        # incoming blocks must not yank the view back to the bottom.
        # Follow-mode resumes once scroll_up reaches 0 via PgDn/wheel-down/
        # submitting input (`_submit` explicitly resets it).
        with self._lock:
            self._blocks.append(ansi_block)
            self._frag_cache.append(_ansi_to_fragments(ansi_block))
            if len(self._blocks) > 4000:
                drop = len(self._blocks) - 4000
                self._blocks = self._blocks[drop:]
                self._frag_cache = self._frag_cache[drop:]

    # -- streaming region ----------------------------------------------------

    def stream_reset(self) -> None:
        with self._lock:
            self._stream_parts = []

    def stream_append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._stream_parts.append(text)

    def stream_commit(self, ansi_block: str) -> None:
        """Replace the streaming region with the final rendered block."""
        with self._lock:
            self._stream_parts = []
        self.append(ansi_block)

    # -- rendering -------------------------------------------------------------

    def tail_fragments(self, height: int) -> list[tuple[str, str]]:
        """Fragments for the last ``height`` lines (stream region included)."""
        with self._lock:
            committed: list[list[tuple[str, str]]] = list(self._frag_cache)
            stream = "".join(self._stream_parts)
        lines: list[list[tuple[str, str]]] = []
        for frags in committed:
            lines.extend(_split_fragments_lines(frags))
        if stream:
            lines.extend(_split_fragments_lines(_ansi_to_fragments(stream)))
        if self.scroll_up > 0:
            start = max(0, len(lines) - height - self.scroll_up)
            end = max(start + 1, len(lines) - self.scroll_up)
            chosen = lines[start:end]
        else:
            chosen = lines[-height:] if height > 0 else []
        out: list[tuple[str, str]] = []
        for ln in chosen:
            out.extend(ln)
            out.append(("", "\n"))
        return out


def _split_fragments_lines(frags: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Split a fragment stream into per-line fragment lists.

    A trailing ``\\n`` does NOT create an extra empty line ("a\\n" → one
    line) — blocks already end with newlines and doubled blank lines would
    space out the transcript.
    """
    lines: list[list[tuple[str, str]]] = [[]]
    for style, text in frags:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                lines.append([])
            if part:
                lines[-1].append((style, part))
    if len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# Approvals — cross-thread y/n/a/o
# ---------------------------------------------------------------------------

@dataclass
class PendingApproval:
    tool: str
    summary: str
    detail: str
    reason: str
    allow_always: bool
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = ""    # y / n / a / o — set by the UI thread


class ApprovalBroker:
    """Tool threads block in :meth:`ask`; the UI answers with y/n/a/o keys."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: PendingApproval | None = None

    @property
    def pending(self) -> PendingApproval | None:
        with self._lock:
            return self._pending

    def ask(
        self, *, tool: str, summary: str, detail: str, reason: str,
        allow_always: bool = True,
    ) -> str:
        req = PendingApproval(
            tool=tool, summary=summary, detail=detail, reason=reason,
            allow_always=allow_always,
        )
        with self._lock:
            self._pending = req
        try:
            # No timeout — the user decides. Ctrl-C answers "n" via the UI.
            req.event.wait()
        finally:
            with self._lock:
                self._pending = None
        return req.decision or "n"

    def answer(self, decision: str) -> None:
        with self._lock:
            req = self._pending
        if req is not None and not req.event.is_set():
            req.decision = decision
            req.event.set()


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------

class TuiRepl:
    """Owns the full-screen Application (UI thread) + the IPC surface."""

    def __init__(
        self,
        *,
        side_panel: SidePanel,
        completer: Any = None,
        history: Any = None,
        bindings: Any = None,
        on_interrupt: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        pt_input: Any = None,
        pt_output: Any = None,
        prefs: UiPrefs | None = None,
    ) -> None:
        self.side_panel = side_panel
        self.prefs = prefs or UiPrefs()
        self.prefs.clamp()
        self.approval = ApprovalBroker()
        self._completer = completer
        self._history = history
        self._user_bindings = bindings
        self._on_interrupt = on_interrupt
        self._on_exit = on_exit
        # Optional prompt_toolkit input/output injection (headless tests).
        self._pt_input = pt_input
        self._pt_output = pt_output

        self.transcript = TranscriptBuffer()
        self._lines: Queue[str] = Queue()
        # Live-region style markers (reset by stream_reset).
        self._stream_model_shown = False
        self._stream_last_kind = ""
        self._stream_reasoning_shown = False
        self._thread: threading.Thread | None = None
        self._app: Any = None
        self._dead = False
        self._last_invalidate = 0.0
        self._input_buffer: Any = None
        self._term_w = 100
        self._term_h = 30
        self._console: Any = None
        self._orig_file: Any = None
        # Startup failure reason (shown by the caller when falling back).
        self.start_error: str = ""

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_app, daemon=True, name="mbridge-tui"
        )
        self._thread.start()

    def stop(self) -> None:
        self._dead = True
        # 恢复被接管的 rich console（回到普通 stdout，退出信息可正常打印）。
        console = self._console
        if console is not None and self._orig_file is not None:
            try:
                console.file = self._orig_file
            except Exception:
                pass
        app = self._app
        if app is not None:
            try:
                app.exit()
            except Exception:
                pass

    def is_alive(self) -> bool:
        return not self._dead and (self._thread is not None and self._thread.is_alive())

    # -- main-thread API -------------------------------------------------------

    def read_input(self) -> str:
        """Block for the next submitted line ('' = bare Enter)."""
        while True:
            if self._dead and self._lines.empty():
                raise EOFError
            try:
                item = self._lines.get(timeout=0.5)
            except Exception:
                if self._thread is not None and not self._thread.is_alive():
                    self._dead = True
                    raise EOFError from None  # 队列超时不是成因，切断异常链
                continue
            if item == "\x00exit":
                raise EOFError
            return item

    def write_ansi(self, text: str) -> None:
        self.transcript.append(text)
        self.invalidate()

    def invalidate(self, *, throttle: bool = True) -> None:
        app = self._app
        if app is None:
            return
        now = time.monotonic()
        if throttle and now - self._last_invalidate < 0.08:
            return
        self._last_invalidate = now
        try:
            app.invalidate()
        except Exception:
            pass

    def stream_reset(self) -> None:
        self._stream_model_shown = False
        self._stream_last_kind = ""
        self._stream_reasoning_shown = False
        self.transcript.stream_reset()
        self.invalidate()

    def stream_append(self, kind: str, text: str) -> None:
        """Live-region delta with per-kind styling (mirrors AssistantStream).

        ``kind="reasoning"`` renders dim italic, ``kind="content"`` plain.
        The region opens with a bold-cyan ``● <model>`` label and a thin dim
        separator marks where thinking ended and the answer began — same
        visual language as the classic bubble, so neither mode looks like a
        raw dump. Deltas arrive as arbitrary chunks; per-delta SGR pairs keep
        the styling correct without needing to track line state.
        """
        if not text:
            return
        pre: list[str] = []
        if not self._stream_model_shown:
            pre.append(f"{_A_MODEL}● {self.side_panel.model_name}{_A_RESET}\n")
            self._stream_model_shown = True
        if kind == "reasoning":
            if self.prefs.stream_thinking:
                styled = _A_THINK + text + _A_THINK_OFF
                self._stream_reasoning_shown = True
            else:
                # /ui 关掉实时思考：delta 静默丢弃（最终 Markdown 里该有的
                # 推理摘要不受影响），标签行保持。
                styled = ""
        else:
            if self._stream_last_kind == "reasoning" and self._stream_reasoning_shown:
                # First content delta after visible thinking — draw the
                # separator (suppressed thinking never drew one).
                pre.append(f"{_A_DIM}────{_A_RESET}\n")
            styled = text
        self._stream_last_kind = kind
        if styled or pre:
            self.transcript.stream_append("".join(pre) + styled)
        self.invalidate()

    def stream_commit(self, ansi_block: str) -> None:
        self.transcript.stream_commit(ansi_block)
        self.invalidate()

    # -- rich console redirection ----------------------------------------------

    def attach_console(self, console: Any) -> None:
        """Redirect a rich Console's writes into the transcript.

        Also narrows the console to the LEFT column width so transcript
        output never runs under the right rail.
        """
        self._console = console
        self._orig_file = console.file
        console.file = _ConsoleProxy(self, console)
        self._sync_console_width(console)

    def _rail_w(self) -> int:
        """Rail width honoring UiPrefs (ratio + cap) with a small floor."""
        return max(
            _MIN_RAIL_PREFS,
            min(self.prefs.rail_max, int(self._term_w * self.prefs.rail_ratio)),
        )

    def _sync_console_width(self, console: Any) -> None:
        try:
            console.width = max(40, self._term_w - self._rail_w() - 1)
        except Exception:
            pass

    # -- UI thread --------------------------------------------------------------

    def _sync_size(self) -> None:
        """Refresh terminal size at render time (Application.run has no
        on_resize hook — querying inside the control callables is the
        supported way and keeps the rail/transcript correct after resizes)."""
        try:
            from prompt_toolkit.application import get_app

            # Size is a NamedTuple: iterating it yields (rows, columns) —
            # tuple-unpacking used to swap them, so the whole layout ran on
            # "terminal height" as width. Read the named fields instead.
            size = get_app().output.get_size()
            self._term_w, self._term_h = int(size.columns), int(size.rows)
            if self._console is not None:
                self._sync_console_width(self._console)
        except Exception:
            pass

    def _run_app(self) -> None:
        try:
            from prompt_toolkit.application import Application
            from prompt_toolkit.buffer import Buffer
            from prompt_toolkit.filters import Condition
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import D, HSplit, Layout, VSplit, Window
            from prompt_toolkit.layout.controls import (
                BufferControl,
                FormattedTextControl,
            )
        except Exception:
            import traceback

            self.start_error = traceback.format_exc()
            self._dead = True
            return

        kb = KeyBindings()
        approval_pending = Condition(lambda: self.approval.pending is not None)

        @kb.add("enter", filter=~approval_pending)
        def _submit(event: Any) -> None:
            buf = event.current_buffer
            text = buf.text
            buf.reset(append_to_history=True)
            # 回显走经典对话语言：右对齐 + 绿色 ▎ 色条（与 render_user_bubble
            # 一致），正文保持默认前景色，不再整行染绿。
            if text:
                shown = text.replace("\x1b", "?")
                bar_cells = 2  # ▎ + space
                left_w = max(20, self._term_w - self._rail_w() - 2)
                if self.prefs.user_align == "right":
                    pad = max(1, left_w - _cells(shown) - bar_cells - 2)
                    line_prefix = " " * pad
                else:
                    line_prefix = ""
                self.transcript.append("")
                self.transcript.append(line_prefix + "\x1b[32m▎\x1b[0m " + shown)
            else:
                self.transcript.append("\x1b[90m(回车 — 唤醒后台任务)\x1b[0m")
            self._lines.put(text)
            self.transcript.scroll_up = 0
            self.invalidate(throttle=False)

        @kb.add("c-c")
        def _cancel(event: Any) -> None:
            if self.approval.pending is not None:
                self.approval.answer("n")
                return
            buf = event.current_buffer
            if buf and buf.text:
                buf.reset()
                return
            if self._on_interrupt is not None:
                try:
                    self._on_interrupt()
                except Exception:
                    pass
                return
            self._lines.put("\x00exit")
            event.app.exit()

        @kb.add("c-d")
        def _eof(event: Any) -> None:
            self._lines.put("\x00exit")
            event.app.exit()

        # Approval answers take over the keyboard while a request is pending.
        for key, decision in (
            ("y", "y"), ("n", "n"), ("a", "a"), ("o", "o"),
        ):
            @kb.add(key, filter=approval_pending)
            def _answer(event: Any, _d=decision) -> None:
                self.approval.answer(_d)

        @kb.add("pageup")
        def _scroll_up(event: Any) -> None:
            self.transcript.scroll_up += max(5, self._term_h // 2)
            self.invalidate(throttle=False)

        @kb.add("pagedown")
        def _scroll_down(event: Any) -> None:
            self.transcript.scroll_up = max(0, self.transcript.scroll_up - max(5, self._term_h // 2))
            self.invalidate(throttle=False)

        # Mouse wheel — packets arrive as the Vt100MouseEvent key (with
        # coordinates), NOT as Keys.ScrollUp: prompt_toolkit routes them to
        # its default Vt100MouseEvent handler first, whose per-window lookup
        # silently returns NotImplemented on our non-scrollable panes. A
        # user-level binding for the same key wins (later entries have
        # priority), so we parse the two common encodings ourselves and give
        # other mouse events (clicks, moves) back via NotImplemented.
        from prompt_toolkit.keys import Keys

        @kb.add(Keys.Vt100MouseEvent)
        def _wheel(event: Any) -> Any:
            data = event.data or ""
            try:
                if data.startswith("\x1b[<"):
                    # xterm SGR: ESC [ < button ; x ; y M/m
                    btn = int(data[3:].split(";", 1)[0])
                elif data.startswith("\x1b[M"):
                    # X10 ("typical"): ESC [ M button+x+y as 3 raw bytes
                    btn = ord(data[3]) - 32
                else:
                    return NotImplemented
            except (IndexError, ValueError):
                return NotImplemented
            if btn == 64:      # wheel up — fine-grained, reading pace
                self.transcript.scroll_up += 3
            elif btn == 65:    # wheel down
                self.transcript.scroll_up = max(0, self.transcript.scroll_up - 3)
            else:
                return NotImplemented  # clicks / motion: default dispatch
            self.invalidate(throttle=False)
            return None

        if self._user_bindings is not None:
            kb = self._merge_bindings(kb, self._user_bindings)

        buf = Buffer(
            completer=self._completer,
            complete_while_typing=True,
            history=self._history or InMemoryHistory(),
            multiline=False,
        )
        self._input_buffer = buf

        transcript_window = Window(
            FormattedTextControl(
                cast(Any, self._transcript_control), show_cursor=False, focusable=False,
            ),
            wrap_lines=True,
            style="",
        )
        label_window = Window(
            FormattedTextControl(
                [("class:prompt", "you ❯ ")], focusable=False, show_cursor=False
            ),
            width=D.exact(6),
            height=D.exact(1),
            dont_extend_height=True,
        )
        input_window = Window(
            BufferControl(buf, focusable=True), wrap_lines=True, height=D.exact(1)
        )
        # 补全下拉菜单（prompt_toolkit 默认提示布局的一部分，裸 BufferControl
        # 不会自带 —— 用 FloatContainer 挂上）。
        try:
            from prompt_toolkit.layout import Float, FloatContainer
            from prompt_toolkit.layout.menus import (
                CompletionsMenu,
                MultiColumnCompletionsMenu,
            )

            input_area: Any = FloatContainer(
                VSplit([label_window, input_window]),
                floats=[
                    Float(xcursor=True, ycursor=True, content=CompletionsMenu()),
                    Float(
                        xcursor=True,
                        ycursor=True,
                        content=MultiColumnCompletionsMenu(),
                    ),
                ],
            )
        except Exception:
            input_area = VSplit([label_window, input_window])
        rail_window = Window(
            FormattedTextControl(
                cast(Any, self._rail_control), show_cursor=False, focusable=False,
            ),
            width=lambda: self._rail_w(),
            wrap_lines=True,
            style="class:rail",
        )
        # 左右之间的竖分隔线（大方一点：栏内内容与对话区有明确边界）。
        try:
            sep_window = Window(
                width=1, char="│", style="class:sep #30363d", dont_extend_width=True,
            )
        except Exception:
            sep_window = None

        left = HSplit([transcript_window, input_area])
        columns = [left] + ([sep_window] if sep_window is not None else []) + [rail_window]
        root = VSplit(columns)

        app: Application[Any] = Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=kb,
            full_screen=True,
            erase_when_done=False,
            # 滚轮翻页依赖终端鼠标上报；pt 会在 enter/leave 时自动切换模式。
            mouse_support=True,
            # We own PgUp/PgDn for transcript paging. The built-in page
            # navigation bindings would otherwise consume PageDown for
            # scrollable-layout paging and outrank our binding on that key
            # (PageUp survives because its built-in no-ops on this layout —
            # an asymmetry that cost a debugging session to find).
            enable_page_navigation_bindings=False,
            input=self._pt_input,
            output=self._pt_output,
        )
        self._app = app
        try:
            app.run()
        except Exception:
            import traceback

            self.start_error = traceback.format_exc()
            self._dead = True
        finally:
            self._dead = True

    @staticmethod
    def _merge_bindings(base: Any, extra: Any) -> Any:
        try:
            from prompt_toolkit.key_binding import merge_key_bindings

            return merge_key_bindings([base, extra])
        except Exception:
            return base

    # -- right rail content ------------------------------------------------------

    def _transcript_control(self) -> list[tuple[str, str]]:
        self._sync_size()
        return self.transcript.tail_fragments(max(3, self._term_h - 3))

    def _rail_control(self) -> list[tuple[str, str]]:
        self._sync_size()
        # 热重载：浏览器侧栏 /ui 面板改了 ui.json → 终端右栏下一帧跟上。
        try:
            self.prefs.maybe_reload()
        except Exception:
            pass
        return self._rail_fragments()

    def _rail_fragments(self) -> list[tuple[str, str]]:
        """Right rail — plain text only, generous spacing, aligned labels.

        Layout (label column aligned, dim rule under each section title)::

            状态
            ──────────────
            目录   D:\\modelbridge
            模型   deepseek-v4-pro
            上下文 4% 2.1k/1.0M
            缓存   16% (768)
            会话   16%

            任务
            ──────────────
            [x] 收购glm
            [ ] 打爆glm-5.3

        Values truncate by *display cells* (CJK-aware), and long pairs that
        used to be crammed onto one line (模型+ctx、缓存+会话) get their own
        row instead of being ellipsized into uselessness.
        """
        w = self._rail_w()
        p = self.prefs
        out: list[tuple[str, str]] = []
        sp = self.side_panel
        pad = "  "
        label_w = 6  # 2 CJK chars + 2 spaces, then value column
        room = w - 4 - label_w  # usable width for kv values (pad + right margin)

        def line(frags: list[tuple[str, str]]) -> None:
            out.extend(frags)
            out.append(("", "\n"))

        def header(title: str) -> None:
            out.append(("", "\n"))
            out.append((f"{p.color_accent} bold", pad + title))
            out.append(("", "\n"))
            # 细分隔线：给每组一个明确的视觉边界，标题不再和内容粘在一起。
            out.append(("#30363d", pad + "─" * min(14, max(8, w // 3))))
            out.append(("", "\n"))

        def wrap(text: str, style: str = "") -> None:
            if _cells(text) > w - 4:
                text = _truncate(text, w - 6) + "…"
            line([(style, pad + text)])

        def kv(label: str, value: str, value_style: str = "") -> None:
            # CJK label occupies 2 cells/char; pad label column to label_w.
            fill = " " * max(1, label_w - len(label) * 2)
            if _cells(value) > room:
                value = _truncate(value, room - 1) + "…"
            line([(p.color_dim, pad + label + fill), (value_style, value)])

        # ── 状态 ────────────────────────────────────────────────────
        if p.show_status:
            header("状态")
            kv("目录", sp.cwd)
            if sp.model_name:
                kv("模型", sp.model_name, f"{p.color_accent} bold")
                if sp.ctx_line:
                    try:
                        pct_val = float(sp.ctx_line.split("%", 1)[0])
                    except ValueError:
                        pct_val = 0.0
                    ctx_color = (
                        p.color_good
                        if pct_val < 60
                        else (p.color_warn if pct_val < 85 else p.color_bad)
                    )
                    kv("上下文", sp.ctx_line, ctx_color)
            if sp.cache_last:
                cache_color = (
                    p.color_warn
                    if sp.cache_last.startswith(("0%", "1", "2", "3"))
                    else p.color_good
                )
                kv("缓存", sp.cache_last, cache_color)
                if sp.cache_session:
                    kv("会话", sp.cache_session)

        # ── 任务 ────────────────────────────────────────────────────
        todos = sp._todos()
        if todos and p.show_tasks:
            header("任务")
            for mark, content, style in todos[:14]:
                color = {
                    "green": p.color_good,
                    "cyan": p.color_accent,
                    "dim": p.color_dim,
                }.get(style, p.color_dim)
                wrap(f"[{mark}] {content}", color)
            if len(todos) > 14:
                wrap(f"… 还有 {len(todos) - 14} 项")

        # ── 后台 ────────────────────────────────────────────────────
        bgs = sp._bg_tasks()
        if bgs and p.show_bg:
            header("后台")
            for mark, txt, style in bgs[:10]:
                color = {
                    "green": p.color_good,
                    "red": p.color_bad,
                    "cyan": p.color_accent,
                }.get(style, p.color_dim)
                wrap(f"{mark} {txt}", color)

        # ── 本轮 ────────────────────────────────────────────────────
        if sp.tools and p.show_tools:
            header("本轮")
            for name, preview, is_error in sp.tools[-12:]:
                mark = "!" if is_error else "+"
                mark_color = p.color_bad if is_error else p.color_good
                prefix_cells = len(pad) + 2  # pad + "X "
                avail = w - prefix_cells - _cells(name) - 1
                prev_disp = ""
                if preview:
                    if avail > 3 and _cells(preview) > avail:
                        prev_disp = " " + _truncate(preview, avail - 1) + "…"
                    elif avail > 0:
                        prev_disp = " " + preview
                line(
                    [
                        (mark_color, pad + mark + " "),
                        (f"{p.color_tool} bold", name),
                        (p.color_dim, prev_disp),
                    ]
                )
            if len(sp.tools) > 12:
                wrap(f"… 共 {len(sp.tools)} 次")

        # ── 审批请求（最高优先级，常显）──────────────────────────────
        pend = self.approval.pending
        if pend is not None:
            header("需要确认")
            wrap(f"{pend.tool}: {pend.summary}", f"{p.color_bad} bold")
            if pend.reason:
                wrap(f"意图: {pend.reason}", p.color_dim)
            wrap("y 同意 · n 拒绝 · a 总是", p.color_warn)
            wrap("o auto (AI 判定)", p.color_warn)

        if p.footer_hint:
            out.append(("", "\n"))
            out.append((p.color_dim, pad + "滚轮逐行 · PgUp/PgDn 翻页 · Ctrl-C 中断"))
        return out


def _term_size(app: Any) -> tuple[int, int]:
    import shutil

    try:
        out = app.output
        size = out.get_size()
        return int(size.columns), int(size.rows)
    except Exception:
        size = shutil.get_terminal_size(fallback=(100, 30))
        return size.columns, size.lines


_ANSI_STRIP_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _cell_w(ch: str) -> int:
    """Terminal cell width of one char (CJK ≈ 2 cells)."""
    return 2 if "\u2e80" <= ch <= "\ufe4f" or "\uff00" <= ch <= "\uffef" else 1


def _cells(text: str) -> int:
    """Terminal cell width of ANSI-free text (CJK ≈ 2 cells)."""
    plain = _ANSI_STRIP_RE.sub("", text)
    return sum(_cell_w(ch) for ch in plain)


def _truncate(text: str, max_cells: int) -> str:
    """Hard-truncate ``text`` to fit ``max_cells`` display cells."""
    w = 0
    for i, ch in enumerate(text):
        cw = _cell_w(ch)
        if w + cw > max_cells:
            return text[:i]
        w += cw
    return text


class _ConsoleProxy:
    """File-like shim: rich Console writes land in the TUI transcript."""

    def __init__(self, tui: TuiRepl, console: Any) -> None:
        self._tui = tui
        self._console = console
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self.isatty = lambda: True
        self.encoding = "utf-8"
        self.errors = "replace"
        self.fileno = None
        self.flush = self._flush

    @property
    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        with self._lock:
            self._buf.append(s)
        return len(s)

    def _flush(self) -> None:
        with self._lock:
            if not self._buf:
                return
            data = "".join(self._buf)
            self._buf = []
        if data:
            self._tui.write_ansi(data)


__all__ = ["TuiRepl", "TranscriptBuffer", "ApprovalBroker", "rail_width"]
