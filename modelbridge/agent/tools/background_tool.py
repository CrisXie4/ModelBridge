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

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ...executor.runner import _decode_child, run_command

#: Default wall-clock cap for one background command. Long enough for real
#: builds/tests; the worker gives up after this and reports a timeout task.
DEFAULT_TIMEOUT_S = 3600.0

#: How much of the output tail rides along in the completion notification.
_NOTIFY_TAIL_CHARS = 2000

#: 运行期输出 tail 的窗口（E8）：worker 读子进程输出时增量维护最近 N 字符
#: 进 ``_BTask.output_tail``，status/wait/侧栏在**运行中**即可看到最近输出。
_RUNNING_TAIL_CHARS = 2_000


@dataclass
class _BTask:
    id: str
    command: str
    status: str = "running"          # running | done | error | timeout
    exit_code: int | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    output: str = ""
    # E8: 运行期最近输出（有界 tail，≤ _RUNNING_TAIL_CHARS）。转后台的
    # 长构建/长测试在 status/面板里实时可见，不必干等完成通知。
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        dur = (self.finished_at or time.monotonic()) - self.started_at
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_s": round(dur, 1),
            "output_chars": len(self.output),
            "output_tail": self.output_tail,
        }

    def append_output_tail(self, text: str) -> None:
        """增量追加运行期 tail（有界；线程安全靠调用方串行化或加锁）。"""
        if not text:
            return
        self.output_tail = (self.output_tail + text)[-_RUNNING_TAIL_CHARS:]


def start_output_pumps(
    proc: subprocess.Popen,
    out_buf: list[bytes],
    err_buf: list[bytes],
    tail_holder: dict,
    tail_lock: threading.Lock,
) -> list[threading.Thread]:
    """为已 spawn 的进程启动 stdout/stderr 泵线程（E8/U9 共用）。

    泵线程增量读原始字节进 ``out_buf/err_buf``，并维护
    ``tail_holder["tail"]``（有界运行期 tail，≤ :data:`_RUNNING_TAIL_CHARS`；
    增量 utf-8 解码器正确处理跨块多字节字符）。read1（而非 read）：有数据
    即返回，read(n) 会等满 n 字节导致“运行期可见”退化成“结束才可见”。
    返回线程列表，调用方在进程结束后 join。
    """
    import codecs

    def _pump(stream, sink: list[bytes]) -> None:
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = stream.read1(8192)
                if not chunk:
                    break
                sink.append(chunk)
                text = dec.decode(chunk)
                if text:
                    with tail_lock:
                        tail_holder["tail"] = (
                            tail_holder["tail"] + text
                        )[-_RUNNING_TAIL_CHARS:]
        except Exception:
            pass  # 流读失败不拖垮任务收尾

    t_out = threading.Thread(target=_pump, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()
    return [t_out, t_err]


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
                task.output_tail = task.output[-_RUNNING_TAIL_CHARS:]
                task.finished_at = time.monotonic()
            self._notify(task)
        threading.Thread(target=work, daemon=True, name=task.id).start()
        return task.to_dict()

    def adopt_process(
        self,
        command: str,
        proc: subprocess.Popen,
        *,
        foreground_started_at: float | None = None,
    ) -> dict[str, Any]:
        """接管一个**已超时但未终止**的前台进程，转后台继续跑到结束。

        run_bash 超时移交（U9，对齐 ZCode bash 的 auto_on_timeout 生命周期）
        用：前台等待超时后不杀进程，把存活 Popen 交给这里——后台泵线程继续
        读输出（E8：增量维护运行期 ``output_tail``，status/面板运行中即可见
        最近输出），结束后走同一套 ``<task-notification>`` 通知链路。命令已
        通过 run_bash 的确认门，这里不新增授权面。

        便捷入口：内部为 ``proc`` 启动输出泵后走 :meth:`adopt_buffers`。
        run_bash 的前台阶段已在跑泵时，应直接调 :meth:`adopt_buffers` ——
        不能再碰 ``communicate``：其超时后残留的孤儿读线程会抢走管道数据。
        """
        out_buf: list[bytes] = []
        err_buf: list[bytes] = []
        tail_holder = {"tail": ""}
        tail_lock = threading.Lock()
        pumps = start_output_pumps(proc, out_buf, err_buf, tail_holder, tail_lock)
        return self.adopt_buffers(
            command, proc, out_buf, err_buf, tail_holder, tail_lock, pumps,
            foreground_started_at=foreground_started_at,
        )

    def adopt_buffers(
        self,
        command: str,
        proc: subprocess.Popen,
        out_buf: list[bytes],
        err_buf: list[bytes],
        tail_holder: dict,
        tail_lock: threading.Lock,
        pumps: list[threading.Thread],
        *,
        foreground_started_at: float | None = None,
    ) -> dict[str, Any]:
        """接管一个**已被泵读取**的进程，登记任务并等其跑到结束（U9/E8）。

        ``out_buf/err_buf/tail_holder`` 是前台阶段已在用的共享容器；本方法
        登记任务、把运行期 tail 同步进来，等进程结束后解码全量输出并发通知。
        """
        task = _BTask(id=f"bg-{uuid.uuid4().hex[:6]}", command=command)
        if foreground_started_at is not None:
            task.started_at = foreground_started_at  # 计时含前台阶段
        with self._lock:
            self._tasks[task.id] = task
        with tail_lock:
            task.output_tail = tail_holder["tail"]

        def work() -> None:
            try:
                # 运行期把泵维护的 tail 近实时同步进任务（status/面板可见）。
                while proc.poll() is None:
                    with tail_lock:
                        task.output_tail = tail_holder["tail"]
                    time.sleep(0.2)
                proc.wait()
                for t in pumps:
                    t.join()
                stdout = _decode_child(b"".join(out_buf))
                stderr = _decode_child(b"".join(err_buf))
                task.exit_code = proc.returncode
                task.status = "done" if proc.returncode == 0 else "error"
                task.output = stdout if not stderr else f"{stdout}\n{stderr}"
                task.output_tail = task.output[-_RUNNING_TAIL_CHARS:]
            except Exception as e:  # noqa: BLE001 — 与 start 的 worker 同口径
                task.status = "error"
                task.output = f"{type(e).__name__}: {e}"
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
