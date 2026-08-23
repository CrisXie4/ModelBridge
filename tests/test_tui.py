"""Full-screen TUI — pure-logic pieces (no Application needed).

* TranscriptBuffer: committed blocks + live stream region, tail view,
  scroll-up pinning.
* ApprovalBroker: cross-thread ask/answer handshake.
* TuiRepl._rail_fragments: vertical right rail — status lines, todo
  checkboxes, background tasks, this-turn tools, pending approval.
* _ConsoleProxy: rich console writes land in the transcript on flush.
"""

from __future__ import annotations

import re
import threading
import time

from modelbridge.agent.live_state import TodoStore
from modelbridge.agent.tools.background_tool import BackgroundTaskManager
from modelbridge.agent.tui import (
    ApprovalBroker,
    TranscriptBuffer,
    _ConsoleProxy,
    rail_width,
)
from modelbridge.agent.ui import SidePanel


# ---------------------------------------------------------------------------
# TranscriptBuffer
# ---------------------------------------------------------------------------

def test_transcript_tail_and_stream_region():
    t = TranscriptBuffer()
    t.append("line-one\n")
    t.append("line-two\n")
    t.stream_append("streaming…")
    frags = t.tail_fragments(height=10)
    text = "".join(seg for _s, seg in frags)
    assert "line-one" in text
    assert "line-two" in text
    assert "streaming…" in text  # stream region renders after committed


def test_transcript_tail_caps_height():
    t = TranscriptBuffer()
    for i in range(50):
        t.append(f"row-{i}\n")
    text = "".join(seg for _s, seg in t.tail_fragments(height=5))
    assert "row-49" in text
    assert "row-45" in text
    assert "row-44" not in text
    assert "row-0" not in text


def test_transcript_scroll_up_pins_history():
    t = TranscriptBuffer()
    for i in range(30):
        t.append(f"row-{i}\n")
    t.scroll_up = 10
    text = "".join(seg for _s, seg in t.tail_fragments(height=5))
    assert "row-19" in text       # 30 - 10 - 1 = newest visible row
    assert "row-15" in text
    assert "row-14" not in text
    assert "row-29" not in text   # the tail itself is scrolled away


def test_transcript_stream_commit_replaces_stream():
    t = TranscriptBuffer()
    t.append("before\n")
    t.stream_append("partial")
    t.stream_commit("final-block\n")
    text = "".join(seg for _s, seg in t.tail_fragments(height=10))
    assert "final-block" in text
    assert "partial" not in text
    assert "before" in text


def test_transcript_append_resets_scroll():
    t = TranscriptBuffer()
    t.append("a\n")
    t.scroll_up = 5
    t.append("b\n")
    assert t.scroll_up == 0


# ---------------------------------------------------------------------------
# ApprovalBroker
# ---------------------------------------------------------------------------

def test_approval_broker_handshake():
    b = ApprovalBroker()
    results: list[str] = []

    def asker():
        results.append(b.ask(tool="write_file", summary="写入", detail="d", reason="r"))

    th = threading.Thread(target=asker)
    th.start()
    deadline = time.time() + 2
    while time.time() < deadline and b.pending is None:
        time.sleep(0.01)
    assert b.pending is not None
    assert b.pending.tool == "write_file"
    b.answer("a")
    th.join(timeout=2)
    assert results == ["a"]
    assert b.pending is None


def test_approval_broker_ctrl_c_answer_n():
    b = ApprovalBroker()
    results: list[str] = []

    def asker():
        results.append(b.ask(tool="run_bash", summary="跑命令", detail="d", reason=""))

    th = threading.Thread(target=asker)
    th.start()
    deadline = time.time() + 2
    while time.time() < deadline and b.pending is None:
        time.sleep(0.01)
    b.answer("n")  # what the c-c handler does
    th.join(timeout=2)
    assert results == ["n"]


# ---------------------------------------------------------------------------
# Right rail fragments
# ---------------------------------------------------------------------------

def _make_tui():
    from modelbridge.agent.tui import TuiRepl

    store = TodoStore()
    store.add("收购glm")
    store.add("打爆glm-5.3")
    store.update_status(1, "done")
    mgr = BackgroundTaskManager()
    tui = TuiRepl(side_panel=SidePanel(cwd="D:/proj", todo_store=store, bg_manager=mgr))
    tui.side_panel.begin_turn("ds")
    tui.side_panel.update_ctx(used_pct=4.0, used_tokens=2100, window=1_000_000)
    tui.side_panel.update_cache(768, 3900)
    tui.side_panel.add_tool("grep", "pattern=x", True)
    return tui


def test_rail_fragments_vertical_sections():
    tui = _make_tui()
    frags = tui._rail_fragments()
    text = "".join(seg for _s, seg in frags)
    # 状态区：对齐的标签列，纯文本无表情
    assert "状态" in text
    assert "目录" in text and "D:/proj" in text
    assert "模型" in text and "ds" in text and "4%" in text and "2.1k/1.0M" in text
    assert "缓存" in text and "16%" in text
    # 任务勾选（纯文本标记）
    assert "任务" in text
    assert "[x] 收购glm" in text
    assert "[ ] 打爆glm-5.3" in text
    # 本轮工具（+ 成功 / ! 失败）
    assert "本轮" in text
    assert "! grep pattern=x" in text
    # 无表情符号
    for emoji in ("📁", "⚡", "✅", "●", "✓", "✗"):
        assert emoji not in text
    # 操作提示
    assert "PgUp/PgDn" in text


def test_rail_shows_pending_approval():
    tui = _make_tui()
    b = tui.approval
    done = threading.Event()

    def asker():
        b.ask(tool="write_file", summary="覆盖 a.py", detail="d", reason="修 bug")
        done.set()

    th = threading.Thread(target=asker, daemon=True)
    th.start()
    deadline = time.time() + 2
    while time.time() < deadline and b.pending is None:
        time.sleep(0.01)
    text = "".join(seg for _s, seg in tui._rail_fragments())
    assert "需要确认" in text
    assert "write_file" in text and "覆盖 a.py" in text
    assert "y 同意" in text and "o auto" in text
    b.answer("y")
    done.wait(timeout=2)


def test_rail_width_bounds():
    assert rail_width(300) == 72      # capped (generous)
    assert rail_width(120) == 48      # two fifths
    assert rail_width(100) == 40
    assert rail_width(60) == 38       # floor for tiny terminals


# ---------------------------------------------------------------------------
# _ConsoleProxy
# ---------------------------------------------------------------------------

def test_console_proxy_flushes_into_transcript():
    tui = _make_tui()

    class _Console:
        pass

    proxy = _ConsoleProxy(tui, _Console())
    proxy.write("hello ")
    assert "".join(seg for _s, seg in tui.transcript.tail_fragments(10)).find("hello") < 0
    proxy.write("world\n")
    proxy.flush()
    text = "".join(seg for _s, seg in tui.transcript.tail_fragments(10))
    assert "hello world" in text


# ---------------------------------------------------------------------------
# Headless end-to-end: the real Application with pipe input + dummy output.
# This is the test class that catches startup crashes (the on_resize TypeError
# shipped silently because nothing exercised app.run).
# ---------------------------------------------------------------------------

def _run_headless(tui, pipe):
    """Drive the app on its thread the way the REPL does."""
    pipe.send_text("hello\r")
    got = tui.read_input()
    pipe.send_text("\r")  # bare enter → "" (bg wake path)
    got_empty = tui.read_input()
    return got, got_empty


def test_full_app_boots_and_reads_input():
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from modelbridge.agent.tui import TuiRepl

    with create_pipe_input() as pipe:
        tui = TuiRepl(
            side_panel=SidePanel(cwd="D:/proj"),
            pt_input=pipe,
            pt_output=DummyOutput(),
        )
        tui.start()
        deadline = time.time() + 5
        while time.time() < deadline and not tui.is_alive():
            if tui.start_error:
                raise AssertionError(f"TUI crashed at startup:\n{tui.start_error}")
            time.sleep(0.05)
        assert tui.is_alive(), (
            f"TUI failed to start:\n{tui.start_error or '(no error captured)'}"
        )
        try:
            pipe.send_text("hello\r")
            line = tui.read_input()
            assert line == "hello"
            pipe.send_text("\r")
            assert tui.read_input() == ""
            # Ctrl-D exits; read_input then raises EOF.
            pipe.send_text("\x04")
            try:
                tui.read_input()
                raised = False
            except EOFError:
                raised = True
            deadline2 = time.time() + 5
            while time.time() < deadline2 and tui._thread.is_alive():
                time.sleep(0.05)
            assert raised
        finally:
            tui.stop()
    # Transcript got the echo of the submitted line (right-aligned user msg:
    # a line of leading spaces + the text — the bare-enter echo follows it).
    text = "".join(seg for _s, seg in tui.transcript.tail_fragments(50))
    assert "hello" in text
    assert re.search(r"(?:^|\n) {10,}hello(?:\n|$)", text), "user bubble not right-aligned"
