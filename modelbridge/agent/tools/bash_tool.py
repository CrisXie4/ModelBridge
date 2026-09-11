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
* ``timeout`` defaults to 30 s, capped at 120 s.

If you need real isolation, run mbridge inside a container yourself.
"""

from __future__ import annotations

from typing import Any

from ...executor.runner import run_command
from ..context import AgentContext, ApprovalDecision
from .base import Tool, ToolResult
from .output_spill import DEFAULT_MAX_CHARS, bound_output

_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0
#: Runner-side capture ceiling (per stream: half of this) — a memory bound
#: for pathological output; the model-visible budget stays at 8k via
#: bound_output, and everything captured is spilled to disk in between.
_RUNNER_MAX_OUTPUT = 200_000


class RunBashTool(Tool):
    name = "run_bash"
    description = (
        "在项目 cwd 内执行一条 shell 命令（支持管道、重定向与 ; && 等组合）。"
        "默认 30 秒超时；超长输出保留头尾预览并落盘，可用 read_file 读取完整内容。"
        "首次调用会请求用户确认；选 always 后仅同类命令前缀（如 git push）"
        "本会话不再询问，其他命令仍会确认。"
        "只有 mbridge 启动时加了 --allow-bash 才会启用此工具。"
    )

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
            return self.err(f"命令超时 ({timeout:.0f}s) 被终止。")

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
