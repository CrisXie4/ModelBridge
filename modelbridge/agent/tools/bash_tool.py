"""run_bash tool — gated behind ``ctx.allow_bash``.

We pass the command verbatim to the platform shell (``cmd`` on Windows,
``sh`` elsewhere). Safety layers, in order:

* ``ctx.allow_bash`` must be true (set via ``mbridge --allow-bash``).
* **Command policy gate** — the model-supplied command goes through the
  same :class:`CommandPolicy` (allowlist / denylist / no metacharacters) as
  the human-facing ``mbridge run``. This runs *before* the confirm prompt so
  a dangerous command is rejected even under ``--yes``. Without it the AI
  path would be strictly more dangerous than the human one.
* Every invocation calls ``ctx.confirm`` (with ``allow_always=False`` so a
  one-off "always" can't silently arm future shell execution).
* Output is truncated to 8 KB combined stdout+stderr.
* Working directory comes from ``ctx.cwd``.
* ``timeout`` defaults to 30 s, capped at 120 s.

If you need real isolation, run mbridge inside a container yourself.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ...executor.command_validator import CommandPolicy, CommandRejected
from ..context import AgentContext
from .base import Tool, ToolResult


_MAX_OUTPUT = 8_000
_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0


class RunBashTool(Tool):
    name = "run_bash"
    description = (
        "在项目 cwd 内执行一条 shell 命令。"
        "默认 30 秒超时；输出截断到 8KB。每次调用都会请求用户确认。"
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
                hint="启动时加 --allow-bash 才允许 AI 执行 shell 命令。",
            )
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return self.err("缺少必填参数 command")
        try:
            timeout = float(args.get("timeout", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT
        timeout = min(max(1.0, timeout), _MAX_TIMEOUT)

        # Command policy gate — same allowlist/denylist as `mbridge run`. Runs
        # before confirm so a banned command (rm -rf, curl|sh, ssh, shell
        # metacharacters) is rejected even when the user passed --yes.
        try:
            CommandPolicy.from_config().validate(command)
        except CommandRejected as e:
            return self.err(
                f"命令被安全策略拒绝: {e.reason}",
                hint="run_bash 复用与 `mbridge run` 相同的白/黑名单；"
                     "如确需该命令，把首个程序加到 config.yaml 的 executor.allowed_commands。",
            )

        # allow_always=False: a one-off "always" must not arm future shell
        # execution — the user should see every command.
        if not ctx.confirm(
            tool=self.name,
            summary=f"run_bash (timeout={timeout:.0f}s, cwd={ctx.cwd})",
            detail=command,
            allow_always=False,
        ):
            return self.err("用户拒绝执行命令。")

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(ctx.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                # On Windows, subprocess uses cmd.exe with shell=True; on POSIX /bin/sh.
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self.err(f"命令超时 ({timeout:.0f}s) 被终止。")
        except OSError as e:
            return self.err(f"无法启动 shell: {e}")

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout + (("\n--- stderr ---\n" + stderr) if stderr.strip() else "")
        truncated = False
        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT]
            truncated = True

        header = f"[exit={proc.returncode}]"
        body = combined.rstrip()
        if truncated:
            body += f"\n\n[... 输出超过 {_MAX_OUTPUT} 字符已截断 ...]"

        result = f"{header}\n{body}" if body else header
        return self.ok(
            result,
            structured={
                "command": command,
                "exit": proc.returncode,
                "stdout_len": len(stdout),
                "stderr_len": len(stderr),
                "truncated": truncated,
            },
        )


__all__ = ["RunBashTool"]
