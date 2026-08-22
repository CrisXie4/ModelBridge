"""run_bash tool — gated behind ``ctx.allow_bash``.

We pass the command verbatim to the platform shell (``cmd`` on Windows,
``sh`` elsewhere). Safety layers, in order:

* ``ctx.allow_bash`` must be true (set via ``mbridge --allow-bash``).
* Every invocation calls ``ctx.confirm`` until the user picks "always"
  (``allow_always=True``: one ALWAYS covers the rest of the session). No
  command allowlist/denylist on this path — the human confirmation is the
  gate, same model as Claude Code.
* Execution goes through :func:`modelbridge.executor.runner.run_command`,
  the same hardened runner as ``mbridge run``: on timeout it kills the
  **whole process tree** (``taskkill /F /T`` on Windows / ``killpg`` on
  POSIX), so a long-running grandchild of ``cmd.exe`` can't keep the pipes
  open and hang the call / leak an orphan process. It also decodes output
  with a UTF-8→locale→GBK fallback.
* Output is truncated to 8000 characters combined stdout+stderr.
* Working directory comes from ``ctx.cwd``.
* ``timeout`` defaults to 30 s, capped at 120 s.

If you need real isolation, run mbridge inside a container yourself.
"""

from __future__ import annotations

from typing import Any

from ...executor.runner import run_command
from ..context import AgentContext
from .base import Tool, ToolResult


_MAX_OUTPUT = 8_000
_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0


class RunBashTool(Tool):
    name = "run_bash"
    description = (
        "在项目 cwd 内执行一条 shell 命令（支持管道、重定向与 ; && 等组合）。"
        "默认 30 秒超时；输出截断到 8000 字符。首次调用会请求用户确认，"
        "选 always 后本会话不再询问。"
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

        # allow_always=True: one "always" arms run_bash for the session —
        # the user explicitly opted into shell access with --allow-bash and
        # can see the exact command in this prompt.
        if not ctx.confirm(
            tool=self.name,
            summary=f"run_bash (timeout={timeout:.0f}s, cwd={ctx.cwd})",
            detail=command,
            allow_always=True,
        ):
            return self.err("用户拒绝执行命令。")

        try:
            res = run_command(
                command,
                cwd=ctx.cwd,
                timeout=timeout,
                max_output=_MAX_OUTPUT,
            )
        except OSError as e:
            return self.err(f"无法启动 shell: {e}")

        if res.timed_out:
            return self.err(f"命令超时 ({timeout:.0f}s) 被终止。")

        stdout = res.stdout
        stderr = res.stderr
        combined = stdout + (("\n--- stderr ---\n" + stderr) if stderr.strip() else "")
        truncated = res.truncated
        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT]
            truncated = True

        header = f"[exit={res.exit_code}]"
        body = combined.rstrip()
        if truncated:
            body += f"\n\n[... 输出超过 {_MAX_OUTPUT} 字符已截断 ...]"

        result = f"{header}\n{body}" if body else header
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
