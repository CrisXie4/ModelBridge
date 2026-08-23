"""Background tasks — long-running commands detached from the agent turn.

The model starts a command (build / test / install …) via the
``background_task`` tool, gets a task id back immediately, and can end its
turn. When the command finishes, :class:`BackgroundTaskManager` renders a
``<task-notification>`` block and hands it to the caller's ``on_complete``
hook — the CLI REPL appends it to the session as a user message and wakes
the agent for a continuation turn.

Safety mirrors :class:`RunBashTool`: every start goes through ``ctx.confirm``
(sharing run_bash's "always" decision), execution uses the same hardened
:func:`executor.runner.run_command`.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ...executor.runner import run_command

#: Default wall-clock cap for one background command. Long enough for real
#: builds/tests; the worker gives up after this and reports a timeout task.
DEFAULT_TIMEOUT_S = 3600.0

#: How much of the output tail rides along in the completion notification.
_NOTIFY_TAIL_CHARS = 2000


@dataclass
class _BTask:
    id: str
    command: str
    status: str = "running"          # running | done | error | timeout
    exit_code: int | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    output: str = ""

    def to_dict(self) -> dict[str, Any]:
        dur = (self.finished_at or time.monotonic()) - self.started_at
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_s": round(dur, 1),
            "output_chars": len(self.output),
        }


class BackgroundTaskManager:
    """Owns the background worker threads and the completion notifications."""

    def __init__(self, on_complete: Callable[[str, _BTask], None] | None = None) -> None:
        self._tasks: dict[str, _BTask] = {}
        self._lock = threading.Lock()
        self._on_complete = on_complete

    def start(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        if cwd is None:
            import os

            cwd = os.getcwd()  # run_command requires a concrete cwd
        task = _BTask(id=f"bg-{uuid.uuid4().hex[:6]}", command=command)
        with self._lock:
            self._tasks[task.id] = task

        def work() -> None:
            try:
                res = run_command(command, cwd=Path(cwd), timeout=timeout, max_output=200_000)
            except Exception as e:  # noqa: BLE001 — surface as task error
                task.status = "error"
                task.output = f"{type(e).__name__}: {e}"
                task.finished_at = time.monotonic()
            else:
                timed_out = bool(getattr(res, "timed_out", False))
                ok = not timed_out and res.exit_code == 0
                task.status = "done" if ok else ("timeout" if timed_out else "error")
                task.exit_code = res.exit_code
                task.output = res.stdout if not res.stderr else f"{res.stdout}\n{res.stderr}"
                task.finished_at = time.monotonic()
            self._notify(task)
        threading.Thread(target=work, daemon=True, name=task.id).start()
        return task.to_dict()

    def _notify(self, task: _BTask) -> None:
        if self._on_complete is None:
            return
        try:
            self._on_complete(_render_notification(task), task)
        except Exception:
            pass  # notification must never take the worker thread down

    def start_confirmed(self, args: dict, *, ctx, tool) -> Any:
        """Parse + confirm + launch one background task (tool-side entry).

        Lives here — next to the runner — so the tool module stays a thin
        dispatcher. The confirmation shares run_bash's "always" decision:
        shell execution is shell execution, whichever tool started it.
        """
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return tool.err("action=start 需要 command 参数")
        if not ctx.confirm(
            tool=tool.name,
            summary=f"后台执行 (cwd={ctx.cwd})",
            detail=command,
            allow_always=True,
            group="run_bash",
        ):
            return tool.err("用户拒绝执行后台命令。")
        try:
            timeout = float(args.get("timeout", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        info = self.start(command, cwd=str(ctx.cwd), timeout=timeout)
        return tool.ok(
            f"已在后台启动任务 {info['id']}。你可以结束本轮（休息），"
            "完成后系统会以 <task-notification> 通知你继续；"
            "或随时用 action=status / wait 查询。",
            structured=info,
        )

    def get(self, task_id: str) -> _BTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        """All tasks as dicts. (Not named ``list`` — that would shadow the
        builtin inside this class's annotations.)"""
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def status(self, task_id: str) -> dict[str, Any] | None:
        t = self.get(task_id)
        return t.to_dict() if t else None

    def wait(self, task_id: str, *, timeout: float = 30.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            t = self.get(task_id)
            if t is None:
                return None
            if t.status != "running":
                return t.to_dict()
            time.sleep(0.2)
        t = self.get(task_id)
        return t.to_dict() if t else None

    def snapshot_for_panel(self) -> list[dict[str, Any]]:
        """Newest-first task list for the right-side status panel."""
        with self._lock:
            return [t.to_dict() for t in reversed(list(self._tasks.values()))]


def _fmt_secs(s: float) -> str:
    if s >= 60:
        return f"{s / 60:.1f}min"
    return f"{s:.0f}s"


def _render_notification(task: _BTask) -> str:
    dur = (task.finished_at or time.monotonic()) - task.started_at
    status_txt = {
        "done": "成功完成 (exit 0)",
        "error": f"失败 (exit {task.exit_code})",
        "timeout": "超时被终止",
    }.get(task.status, task.status)
    tail = task.output[-_NOTIFY_TAIL_CHARS:]
    if len(task.output) > _NOTIFY_TAIL_CHARS:
        tail = f"…\n{tail}"
    return "\n".join([
        "<task-notification>",
        f"<task-id: {task.id}>",
        f"<command: {task.command}>",
        f"<status: {status_txt} · 用时 {_fmt_secs(dur)}>",
        "<output tail:",
        tail or "(无输出)",
        "</task-notification>",
    ])


def _fmt_task(info: dict[str, Any]) -> str:
    mark = {"running": "▍", "done": "✓", "error": "✗", "timeout": "⏱"}.get(
        info["status"], "?"
    )
    cmd = info["command"]
    if len(cmd) > 60:
        cmd = f"{cmd[:60]}…"
    return f"{mark} {info['id']}  {info['status']}  {info['duration_s']}s  {cmd}"


__all__ = ["BackgroundTaskManager"]
