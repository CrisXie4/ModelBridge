"""run_bash tool — gated behind ``ctx.allow_bash``.

We pass the command verbatim to the platform shell (``cmd`` on Windows,
``sh`` elsewhere). Safety layers, in order:

* ``ctx.allow_bash`` must be true (set via ``mbridge --allow-bash``).
* Every invocation calls ``ctx.confirm`` until covered by an approval.
  "always" is **pattern-scoped** (opencode-style, see
  :mod:`..command_patterns`): approving ``git push`` records the prefix
  pattern ``git push *``, so future commands auto-approve only when every
  pipeline segment matches a recorded pattern — one "always" widens by
  one level, not to the whole tool. No allowlist/denylist on top; the
  human confirmation is the gate, same model as Claude Code.
* Execution goes through :func:`modelbridge.executor.runner.run_command`,
  the same hardened runner as ``mbridge run``: on timeout it kills the
  **whole process tree** (``taskkill /F /T`` on Windows / ``killpg`` on
  POSIX), so a long-running grandchild of ``cmd.exe`` can't keep the pipes
  open and hang the call / leak an orphan process. It also decodes output
  with a UTF-8→locale→GBK fallback.
* Output is bounded to 8000 characters combined stdout+stderr; the full
  output is spilled to ``~/.modelbridge/tool-output/`` and the model gets
  a head+tail preview plus the spill path (see :mod:`.output_spill`).
* Working directory comes from ``ctx.cwd``.
* ``timeout`` defaults to 30 s, capped at 120 s. On timeout a non-``sleep``
  command is **not killed** when a :class:`BackgroundTaskManager` is wired
  in: the live process is adopted into the background (it keeps running)
  and the tool returns a task id + completion-notification contract — a
  long build loses nothing. ``sleep``-style commands and manager-less
  setups keep the old kill-and-report behaviour.

If you need real isolation, run mbridge inside a container yourself.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

from ...executor.runner import CommandResult, _decode_child, run_command
from ..context import AgentContext, ApprovalDecision
from .base import Tool, ToolResult
from .background_tool import BackgroundTaskManager
from .output_spill import DEFAULT_MAX_CHARS, bound_output

_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0
#: Runner-side capture ceiling (per stream: half of this) — a memory bound
#: for pathological output; the model-visible budget stays at 8k via
#: bound_output, and everything captured is spilled to disk in between.
_RUNNER_MAX_OUTPUT = 200_000


def _auto_background_eligible(command: str) -> bool:
    """超时是否值得移交后台（对齐 ZCode bash-background-policy：首 token 为
    ``sleep`` 的命令不转后台——sleep 超时只是"等一等"，转后台没有意义）。"""
    first = command.strip().split()
    return bool(first) and first[0] != "sleep"


class RunBashTool(Tool):
    name = "run_bash"
    description = (
        "在项目 cwd 内执行一条 shell 命令（支持管道、重定向与 ; && 等组合）。"
        "默认 30 秒超时；超时且非 sleep 类命令时不杀进程，自动转入后台继续"
        "执行并返回任务编号，完成时以 <task-notification> 通知。超长输出保留"
        "头尾预览并落盘，可用 read_file 读取完整内容。首次调用会请求用户确认；"
        "选 always 后仅同类命令前缀（如 git push）本会话不再询问，其他命令"
        "仍会确认。只有 mbridge 启动时加了 --allow-bash 才会启用此工具。"
    )

    def __init__(self, manager: BackgroundTaskManager | None = None) -> None:
        # 超时移交后台用的任务管理器（REPL 注入；None 时超时退回旧行为：
        # 杀进程树并报错）。
        self.manager = manager

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令。"},
                "timeout": {
                    "type": "number",
                    "description": "超时秒数 (默认 30，最大 120)。",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        if not ctx.allow_bash:
            return self.err(
                "run_bash 未启用。",
                hint="本次会话以 --no-allow-bash 启动，不允许 AI 执行 shell 命令。",
            )
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return self.err("缺少必填参数 command")
        try:
            timeout = float(args.get("timeout", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT
        timeout = min(max(1.0, timeout), _MAX_TIMEOUT)

        # Pattern-scoped "always": a previous ALWAYS decision covers this
        # command only when every pipeline segment matches a recorded
        # prefix pattern ("git push *" from an approved git push, ...).
        if not ctx.bash_allowed(command):
            # allow_always=False — we handle the ALWAYS widening ourselves
            # (record patterns, not arm the whole tool).
            if not ctx.confirm(
                tool=self.name,
                summary=f"run_bash (timeout={timeout:.0f}s, cwd={ctx.cwd})",
                detail=command,
                allow_always=False,
            ):
                message = "用户拒绝执行命令。"
                if ctx.last_feedback:
                    message += (
                        f"\n用户拒绝时的反馈：{ctx.last_feedback}\n"
                        "请根据反馈调整方案，不要原样重试同一命令。"
                    )
                return self.err(message)
            if ctx.last_decision == ApprovalDecision.ALWAYS:
                ctx.approve_bash(command)

        if self.manager is not None and _auto_background_eligible(command):
            return self._run_with_background_handoff(command, timeout=timeout, ctx=ctx)

        try:
            res = run_command(
                command,
                cwd=ctx.cwd,
                timeout=timeout,
                max_output=_RUNNER_MAX_OUTPUT,
            )
        except OSError as e:
            return self.err(f"无法启动 shell: {e}")

        if res.timed_out:
            # 走到这里的是 sleep 类命令或未注入 manager 的场景：维持旧行为
            # （杀进程树并报错），移交后台对它们没有意义/不可用。
            return self.err(f"命令超时 ({timeout:.0f}s) 被终止。")
        return self._present(command, res, ctx)

    def _run_with_background_handoff(
        self, command: str, *, timeout: float, ctx: AgentContext
    ) -> ToolResult:
        """超时不杀进程，移交 :class:`BackgroundTaskManager` 继续跑（U9）。

        对齐 ZCode bash 的 auto_on_timeout 生命周期：spawn 后立刻起输出泵
        （E8），前台等到 deadline —— 未结束就把泵与缓冲原封移交给后台任务
        （不经过 communicate：其超时孤儿读线程会抢走管道数据），本次调用
        立刻返回任务号 + 完成通知约定。命令已过确认门，无新增授权面。
        """
        # 与 executor.runner 相同的 Popen 形态（新进程组/会话 + 独立管道），
        # 保证移交后的进程不挂在当前调用的生命周期上。
        popen_kwargs: dict = dict(
            cwd=str(ctx.cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            popen_kwargs["start_new_session"] = True

        started = time.monotonic()
        try:
            proc = subprocess.Popen(command, **popen_kwargs)
        except OSError as e:
            return self.err(f"无法启动 shell: {e}")

        from .background_tool import start_output_pumps

        out_buf: list[bytes] = []
        err_buf: list[bytes] = []
        tail_holder: dict = {"tail": ""}
        tail_lock = threading.Lock()
        pumps = start_output_pumps(proc, out_buf, err_buf, tail_holder, tail_lock)

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True

        if timed_out:
            # 关键差异：不 kill —— 泵与缓冲原封移交，进程继续在后台跑完，
            # 本调用立刻返回任务号。
            manager = self.manager
            if manager is None:  # pragma: no cover — dispatch 已保证非空
                return self.err("后台任务管理器不可用，无法移交。")
            info = manager.adopt_buffers(
                command, proc, out_buf, err_buf, tail_holder, tail_lock, pumps,
                foreground_started_at=started,
            )
            return self.ok(
                f"命令超时 ({timeout:.0f}s)，未终止 —— 已转入后台继续执行"
                f"（任务 {info['id']}）。完成时会有 <task-notification> 通知；"
                "也可用 background_task action=status/wait 查询。",
                structured={"command": command, "backgrounded": True, **info},
            )

        pumps[0].join()
        pumps[1].join()
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = _decode_child(b"".join(out_buf))
        stderr = _decode_child(b"".join(err_buf))
        half = max(0, _RUNNER_MAX_OUTPUT // 2)
        truncated = False
        if stdout and len(stdout) > half:
            stdout = stdout[:half] + f"\n[... stdout truncated at {half} chars ...]"
            truncated = True
        if stderr and len(stderr) > half:
            stderr = stderr[:half] + f"\n[... stderr truncated at {half} chars ...]"
            truncated = True
        res = CommandResult(
            command=command,
            stdout=stdout or "",
            stderr=stderr or "",
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            truncated=truncated,
            timed_out=False,
        )
        return self._present(command, res, ctx)

    def _present(self, command: str, res: CommandResult, ctx: AgentContext) -> ToolResult:
        """把一次成功结束的 CommandResult 渲染成工具结果（与旧路径同构）。"""
        stdout = res.stdout
        stderr = res.stderr
        combined = stdout + (("\n--- stderr ---\n" + stderr) if stderr.strip() else "")
        body, bounded = bound_output(
            combined.rstrip(),
            tool=self.name,
            max_chars=DEFAULT_MAX_CHARS,
            delegable=ctx.delegable,
        )
        truncated = bounded or res.truncated

        result = f"[exit={res.exit_code}]\n{body}" if body else f"[exit={res.exit_code}]"
        return self.ok(
            result,
            structured={
                "command": command,
                "exit": res.exit_code,
                "stdout_len": len(stdout),
                "stderr_len": len(stderr),
                "truncated": truncated,
            },
        )


__all__ = ["RunBashTool"]
