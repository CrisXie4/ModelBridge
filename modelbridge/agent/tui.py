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
from typing import Any, Callable

from .ui import SidePanel

#: Right rail width: generous — ~2/5 of the screen, clamped for tiny terms.
_MIN_RAIL = 38
_MAX_RAIL = 72


def rail_width(term_width: int) -> int:
    return max(_MIN_RAIL, min(_MAX_RAIL, term_width * 2 // 5))


# ---------------------------------------------------------------------------
# Transcript — committed blocks + the live streaming region
# ---------------------------------------------------------------------------

def _ansi_to_fragments(block: str) -> list[tuple[str, str]]:
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    try:
        return [(style or "", text) for style, text in to_formatted_text(ANSI(block))]
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
        with self._lock:
            self._blocks.append(ansi_block)
            self._frag_cache.append(_ansi_to_fragments(ansi_block))
            self.scroll_up = 0
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
    ) -> None:
        self.side_panel = side_panel
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
                    raise EOFError
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
        self.transcript.stream_reset()
        self.invalidate()

    def stream_append(self, _kind: str, text: str) -> None:
        self.transcript.stream_append(text)
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

    def _sync_console_width(self, console: Any) -> None:
        try:
            console.width = max(40, self._term_w - rail_width(self._term_w) - 1)
        except Exception:
            pass

    # -- UI thread --------------------------------------------------------------

    def _sync_size(self) -> None:
        """Refresh terminal size at render time (Application.run has no
        on_resize hook — querying inside the control callables is the
        supported way and keeps the rail/transcript correct after resizes)."""
        try:
            from prompt_toolkit.application import get_app

            w, h = get_app().output.get_size()
            self._term_w, self._term_h = int(w), int(h)
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
            # 回显走经典对话语言：用户消息右对齐（绿色），AI 输出靠左。
            if text:
                left_w = max(20, self._term_w - rail_width(self._term_w) - 2)
                pad = max(1, left_w - _cells(text) - 2)
                self.transcript.append("")
                self.transcript.append(
                    " " * pad + "\x1b[32m" + text.replace("\x1b", "?") + "\x1b[0m"
                )
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
                self._transcript_control, show_cursor=False, focusable=False,
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
            from prompt_toolkit.layout import FloatContainer
            from prompt_toolkit.layout.menus import (
                CompletionMenu,
                MultiColumnCompletionMenu,
            )

            input_area: Any = FloatContainer(
                VSplit([label_window, input_window]),
                floats=[
                    CompletionMenu(),
                    MultiColumnCompletionMenu(),
                ],
            )
        except Exception:
            input_area = VSplit([label_window, input_window])
        rail_window = Window(
            FormattedTextControl(
                self._rail_control, show_cursor=False, focusable=False,
            ),
            width=lambda: rail_width(self._term_w),
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

        app = Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=kb,
            full_screen=True,
            erase_when_done=False,
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
        return self._rail_fragments()

    def _rail_fragments(self) -> list[tuple[str, str]]:
        """Right rail — plain text only, generous spacing, aligned labels.

        Layout (label column aligned, blank line between sections)::

            状态
            目录   D:\\modelbridge
            模型   ds  4% 2.1k/1.0M
            缓存   16% (768)  会话 16%

            任务
            [x] 收购glm
            [ ] 打爆glm-5.3
        """
        w = rail_width(self._term_w)
        out: list[tuple[str, str]] = []
        sp = self.side_panel
        pad = "  "
        label_w = 6  # 2 CJK chars + 2 spaces, then value column

        def line(frags: list[tuple[str, str]]) -> None:
            out.extend(frags)
            out.append(("", "\n"))

        def header(title: str) -> None:
            out.append(("", "\n"))
            out.append(("#58a6ff bold", pad + title))
            out.append(("", "\n"))

        def wrap(text: str, style: str = "") -> None:
            if len(text) > w - 5:
                text = text[: w - 6] + "…"
            line([(style, pad + text)])

        def kv(label: str, value: str, value_style: str = "") -> None:
            # CJK label occupies 2 cells/char; pad label column to label_w.
            fill = " " * max(1, label_w - len(label) * 2)
            room = w - 4 - label_w
            if len(value) > room:
                value = value[: max(1, room - 1)] + "…"
            line([("#8a8f98", pad + label + fill), (value_style, value)])

        # ── 状态 ────────────────────────────────────────────────────
        header("状态")
        kv("目录", sp.cwd)
        if sp.model_name:
            value = f"{sp.model_name}  {sp.ctx_line}".strip()
            kv("模型", value)
        if sp.cache_last:
            sess = f"  会话 {sp.cache_session}" if sp.cache_session else ""
            kv("缓存", f"{sp.cache_last}{sess}")

        # ── 任务 ────────────────────────────────────────────────────
        todos = sp._todos()
        if todos:
            header("任务")
            for mark, content, style in todos[:14]:
                color = {"green": "#3fb950", "cyan": "#58a6ff", "dim": "#8a8f98"}.get(style, "#8a8f98")
                wrap(f"[{mark}] {content}", color)
            if len(todos) > 14:
                wrap(f"… 还有 {len(todos) - 14} 项")

        # ── 后台 ────────────────────────────────────────────────────
        bgs = sp._bg_tasks()
        if bgs:
            header("后台")
            for mark, txt, style in bgs[:10]:
                color = {"green": "#3fb950", "red": "#f85149", "cyan": "#58a6ff"}.get(style, "#8a8f98")
                wrap(f"{mark} {txt}", color)

        # ── 本轮 ────────────────────────────────────────────────────
        if sp.tools:
            header("本轮")
            for name, preview, is_error in sp.tools[-12:]:
                mark = "!" if is_error else "+"
                color = "#f85149" if is_error else "#3fb950"
                wrap(f"{mark} {name} {preview}".strip(), color)
            if len(sp.tools) > 12:
                wrap(f"… 共 {len(sp.tools)} 次")

        # ── 审批请求（最高优先级，常显）──────────────────────────────
        pend = self.approval.pending
        if pend is not None:
            header("需要确认")
            wrap(f"{pend.tool}: {pend.summary}", "#f85149")
            if pend.reason:
                wrap(f"意图: {pend.reason}", "#8a8f98")
            wrap("y 同意 · n 拒绝 · a 总是", "#d29922")
            wrap("o auto (AI 判定)", "#d29922")

        out.append(("", "\n"))
        out.append(("#8a8f98", pad + "PgUp/PgDn 滚动 · Ctrl-C 中断"))
        return out


def _term_size(app: Any) -> tuple[int, int]:
    import shutil

    try:
        out = app.output
        w, h = out.get_size()
        return w, h
    except Exception:
        size = shutil.get_terminal_size(fallback=(100, 30))
        return size.columns, size.lines


_ANSI_STRIP_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _cells(text: str) -> int:
    """Terminal cell width of ANSI-free text (CJK ≈ 2 cells)."""
    plain = _ANSI_STRIP_RE.sub("", text)
    w = 0
    for ch in plain:
        w += 2 if "\u2e80" <= ch <= "\ufe4f" or "\uff00" <= ch <= "\uffef" else 1
    return w


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
