"""2026-08-22 round 2 — side panel, background tasks, wake-ups, video input.

* SidePanel: sections render (cwd / model+ctx / cache / todos-as-checkboxes /
  bg tasks / this-turn tools), cache accumulates across calls.
* BackgroundTaskManager: real command round trip + notification format;
  tool confirmations share run_bash's group (dispatched via ToolRegistry,
  same as the real agent loop).
* run_interactive wake-ups: a drained background notification runs a
  continuation turn.
* Video: video_url blocks, capability gates (ensure_video), mention
  resolution, read_file video handling.
"""

from __future__ import annotations

import re
import sys
import time

import pytest

from modelbridge.agent.context import AgentContext, ApprovalDecision, auto_yes
from modelbridge.agent.live_state import TodoStore
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.session import Session
from modelbridge.agent.ui import SidePanel, render_side_panel
from modelbridge.agent.tools.background_tool import (
    BackgroundTaskManager,
    _render_notification,
)
from modelbridge.agent.tools.background_task_tool import BackgroundTaskTool


def _ctx(tmp_path, *, approve=auto_yes):
    policy = PathPolicy(allowed_dirs=[tmp_path.resolve()], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path.resolve(), approve=approve)


def _run_bg(mgr: BackgroundTaskManager, task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = mgr.status(task_id)
        if st is None or st["status"] != "running":
            return st
        time.sleep(0.05)
    return mgr.status(task_id)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(console_output: str) -> str:
    """Strip ANSI styling so segment boundaries don't break assertions."""
    return _ANSI_RE.sub("", console_output)


def _dispatch_start(mgr: BackgroundTaskManager, ctx, call_id: str):
    """Start a background task through the registry, like the agent loop."""
    import json as _json

    from modelbridge.agent.tools import ToolRegistry, parse_tool_calls

    reg = ToolRegistry()
    reg.register(BackgroundTaskTool(mgr))
    fn_args = {"action": "start"}
    fn_args["command"] = "echo hi"
    raw_calls = [{
        "id": call_id, "type": "function",
        "function": {"name": "background_task", "arguments": _json.dumps(fn_args)},
    }]
    return reg.dispatch(parse_tool_calls(raw_calls)[0], ctx)


# ---------------------------------------------------------------------------
# SidePanel
# ---------------------------------------------------------------------------

def test_side_panel_sections(tmp_path):
    store = TodoStore()
    store.add("收购glm")
    store.add("打爆glm-5.3")
    store.update_status(1, "done")
    mgr = BackgroundTaskManager()
    p = SidePanel(cwd=str(tmp_path), todo_store=store, bg_manager=mgr)
    p.begin_turn("deepseek-v4-flash")
    p.update_ctx(used_pct=4.0, used_tokens=35_000, window=1_000_000)
    p.update_cache(12_300, 45_600)
    p.update_cache(30_000, 10_000)   # session accumulates
    p.add_tool("read_file", "path=a.py", False)
    p.add_tool("grep", "pattern=x", True)

    # ── bottom status bar segments (the persistent toolbar) ─────────────
    segs = p.status_items()
    seg_text = "  ".join(t for _s, t in segs)
    assert any(t.startswith("目录 ") for _s, t in segs)      # cwd
    assert "模型 deepseek-v4-flash" in seg_text and "4%" in seg_text
    assert "35.0k/1.0M" in seg_text
    assert "缓存 75%" in seg_text                             # last call 30k/40k
    assert "会话 43%" in seg_text                             # 42.3k/97.9k
    assert "计划 1/2" in seg_text

    # ── the after-turn rail print (render_side_panel): rail-only box ────
    import io

    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True)
    render_side_panel(console, p)
    out = _plain(buf.getvalue())
    assert "活动" in out                     # rail title
    assert "[x] 收购glm" in out              # done todo — plain-text checkbox
    assert "[ ] 打爆glm-5.3" in out          # pending todo
    assert "+ read_file" in out
    assert "! grep" in out
    # status trio moved to the bottom bar — must NOT duplicate in the rail
    assert "📁" not in out
    assert "⚡" not in out

    # ── the full panel (live dual-column during turns) still has both ──
    buf2 = io.StringIO()
    console2 = Console(file=buf2, width=120, force_terminal=True)
    console2.print(p.render(console2))
    full = _plain(buf2.getvalue())
    assert "目录" in full and "缓存" in full and "[x] 收购glm" in full


def test_side_panel_empty_rail_prints_nothing(tmp_path):
    """No todos / bg / tools → the after-turn rail print is silent (the
    bottom status bar already covers the state)."""
    p = SidePanel(cwd=str(tmp_path))
    p.begin_turn("m")
    import io

    from rich.console import Console

    buf = io.StringIO()
    render_side_panel(Console(file=buf, width=100, force_terminal=True), p)
    assert buf.getvalue() == ""


def test_side_panel_lists_background_tasks(tmp_path):
    mgr = BackgroundTaskManager()
    t1 = mgr.start("echo quick")
    p = SidePanel(cwd=str(tmp_path), bg_manager=mgr)
    _run_bg(mgr, t1["id"])
    import io

    from rich.console import Console

    buf = io.StringIO()
    render_side_panel(Console(file=buf, width=100, force_terminal=True), p)
    out = _plain(buf.getvalue())
    assert "后台" in out
    assert t1["id"] in out
    assert "echo quick" in out
    # running tasks also surface in the bottom status bar
    mgr2 = BackgroundTaskManager()
    mgr2.start("echo slow")
    p2 = SidePanel(cwd=str(tmp_path), bg_manager=mgr2)
    seg_text = "  ".join(t for _s, t in p2.status_items())
    assert "后台 1" in seg_text


# ---------------------------------------------------------------------------
# BackgroundTaskManager
# ---------------------------------------------------------------------------

def test_bg_manager_runs_command_and_notifies():
    done: list[str] = []
    mgr = BackgroundTaskManager(on_complete=lambda text, task: done.append(text))
    info = mgr.start("echo hello-bg")
    assert info["status"] == "running"
    st = _run_bg(mgr, info["id"])
    assert st["status"] == "done", st
    assert st["exit_code"] == 0
    assert len(done) == 1
    notice = done[0]
    assert notice.startswith("<task-notification>")
    assert "<task-id: " + info["id"] + ">" in notice
    assert "hello-bg" in notice


def test_bg_manager_failure_reports_error_status():
    # A constant failing command per platform — no string building at all.
    fail_cmd = "cmd /c exit 3" if sys.platform == "win32" else "false"
    mgr = BackgroundTaskManager()
    info = mgr.start(fail_cmd)
    st = _run_bg(mgr, info["id"])
    assert st["status"] == "error"
    assert isinstance(st["exit_code"], int) and st["exit_code"] != 0


def test_bg_notification_renders_output_tail():
    from modelbridge.agent.tools.background_tool import _BTask

    t = _BTask(id="bg-x", command="make all", status="error", exit_code=2,
               finished_at=time.monotonic())
    t.output = "line1\n" + "x" * 5000
    t.started_at = t.finished_at - 65.0
    text = _render_notification(t)
    assert "<task-notification>" in text
    assert "exit 2" in text
    assert "…" in text          # tail marker for the long output
    assert len(text) < 3000


def test_bg_tool_shares_run_bash_always(tmp_path):
    """One "always" on run_bash covers background_task starts too."""
    asked: list[str] = []

    def approve(**kw):
        asked.append(kw.get("tool"))
        return ApprovalDecision.ALWAYS

    ctx = _ctx(tmp_path, approve=approve)
    ctx._auto_approved.add("run_bash")  # armed by a prior run_bash "always"
    mgr = BackgroundTaskManager()
    res = _dispatch_start(mgr, ctx, "c1")
    assert not res.is_error, res.content
    assert asked == []          # group memory applied — no prompt
    st = _run_bg(mgr, res.structured["id"])
    assert st["status"] == "done"


def _no_approve(**kw):
    return ApprovalDecision.NO


def test_bg_tool_denied_without_confirm(tmp_path):
    ctx = _ctx(tmp_path, approve=_no_approve)
    mgr = BackgroundTaskManager()
    res = _dispatch_start(mgr, ctx, "c2")
    assert res.is_error
    assert mgr.list_tasks() == []


# ---------------------------------------------------------------------------
# run_interactive wake-ups
# ---------------------------------------------------------------------------

def test_pending_wake_runs_continuation_turn(monkeypatch, tmp_path):
    """After a turn, a drained bg notification triggers another model turn."""
    import modelbridge.agent.loop as loop
    from modelbridge.agent.tools import ToolRegistry
    from modelbridge.models import ModelEntry, ProviderType

    turns: list[int] = []

    class _FakeResp:
        content = "ok"
        tool_calls = None
        finish_reason = "stop"
        reasoning_content = None
        raw_message = {}
        usage = None

    class _Provider:
        def chat(self, request, *, timeout, verbose_label=None):
            turns.append(len(request.messages))
            return _FakeResp()

    monkeypatch.setattr(loop, "get_provider", lambda e: _Provider())
    monkeypatch.setattr(
        loop, "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE,
            model="f", base_url="http://x/v1",
        ),
    )
    monkeypatch.setattr(
        "modelbridge.agent.compact.maybe_auto_compact", lambda *a, **k: None
    )

    session = Session(model_name="fake")
    ctx = AgentContext(policy=PathPolicy([], []), cwd=tmp_path, approve=auto_yes)

    # The wake callback appends the notification (as the CLI does) and says go.
    wakes = [True, False]

    def pending_wake():
        if wakes:
            if not wakes.pop(0):
                return False
            from modelbridge.schemas import ChatMessage

            session.messages.append(ChatMessage(role="user", content="<task-notification>"))
            return True
        return False

    state = {"inputs": ["go"]}

    def read_input():
        if state["inputs"]:
            return state["inputs"].pop(0)
        raise EOFError()

    loop.run_interactive(
        session=session, ctx=ctx, registry=ToolRegistry(),
        model_name="fake", read_input=read_input, pending_wake=pending_wake,
    )
    # Two model calls: the user turn + the background continuation.
    assert len(turns) == 2


# ---------------------------------------------------------------------------
# Video input
# ---------------------------------------------------------------------------

def test_video_block_from_path(tmp_path):
    from modelbridge import images

    f = tmp_path / "demo.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    blk = images.video_block_from_path(str(f))
    assert blk["type"] == "video_url"
    assert blk["video_url"]["url"].startswith("data:video/mp4;base64,")


def test_video_too_large_rejected(tmp_path, monkeypatch):
    from modelbridge import images

    f = tmp_path / "big.mkv"
    f.write_bytes(b"x" * 64)
    monkeypatch.setattr(images, "MAX_VIDEO_BYTES", 32)
    with pytest.raises(images.ImageError):
        images.video_block_from_path(str(f))


def test_ensure_video_gates():
    from modelbridge import images

    with pytest.raises(images.ImageError):
        images.ensure_video(has_videos=True, model_is_vision=False,
                            model_is_video=False, model_name="m")
    with pytest.raises(images.ImageError):
        images.ensure_video(has_videos=True, model_is_vision=True,
                            model_is_video=False, model_name="m")
    images.ensure_video(has_videos=True, model_is_vision=True,
                        model_is_video=True, model_name="m")
    images.ensure_video(has_videos=False, model_is_vision=False,
                        model_is_video=False, model_name="m")


def test_video_mention_rides_inline_block(tmp_path):
    from modelbridge.agent.mentions import (
        build_injection_messages,
        collect_video_blocks,
        resolve_mentions,
    )
    from modelbridge.project.file_index import FileEntry, FileIndex

    (tmp_path / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 32)
    entry = FileEntry(relpath="clip.mp4", is_dir=False)
    index = FileIndex(root=tmp_path, entries=[entry])
    resolved = resolve_mentions("描述一下 @clip.mp4 的内容", index, project_root=tmp_path)
    vids = collect_video_blocks(resolved)
    assert len(vids) == 1
    assert vids[0]["type"] == "video_url"
    # Video must NOT become a text injection message.
    assert build_injection_messages(resolved) == []


def test_read_file_video_gates(tmp_path):
    """read_file's video path: capability gates + video_url extra message."""
    from modelbridge.agent.tools.file_tools import ReadFileTool

    fname = "clip2.webm"
    f = tmp_path / fname
    f.write_bytes(b"\x1aE\xdf\xa3fake")
    ctx = _ctx(tmp_path)

    ctx.model_is_vision = False
    res = ReadFileTool()._read_video(f, ctx)
    assert "vision" in res.content

    ctx.model_is_vision = True
    ctx.model_is_video = False
    res = ReadFileTool()._read_video(f, ctx)
    assert "不支持视频" in res.content

    ctx.model_is_video = True
    res2 = ReadFileTool()._read_video(f, ctx)
    assert "已加载视频" in res2.content
    # The video block rides as an extra user message.
    assert res2.extra_messages
    block = res2.extra_messages[0].content[1]
    assert block["type"] == "video_url"
