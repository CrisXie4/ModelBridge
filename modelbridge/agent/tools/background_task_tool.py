"""``background_task`` — detach long-running commands from the agent turn.

Thin dispatch over :class:`BackgroundTaskManager` (``background_tool.py``):
the start path — argument parsing, the run_bash-grade confirmation, and the
hardened runner launch — lives entirely in the manager next to the executor
it uses. This module only routes the action and formats query results.
"""

from __future__ import annotations

from typing import Any

from ..context import AgentContext
from .background_tool import BackgroundTaskManager, _fmt_task
from .base import Tool, ToolResult


class BackgroundTaskTool(Tool):
    """``background_task`` — start/poll long commands without blocking the turn."""

    name = "background_task"
    description = (
        "在后台跑一条耗时命令（比如构建、测试、安装），马上拿到任务编号，"
        "本轮对话不被卡住。命令完成后系统会推送 <task-notification> 通知，"
        "到时你再接着处理即可，不必反复轮询——可以先结束本轮休息。\n"
        "action=start：启动，command 填要跑的命令，timeout 可选（秒，缺省 3600）。\n"
        "action=list：列出所有后台任务。action=status：按任务编号查一个。\n"
        "action=wait：同步等它跑完（timeout 缺省 30 秒）。"
    )

    def __init__(self, manager: BackgroundTaskManager) -> None:
        self.manager = manager

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "status", "list", "wait"],
                    "description": "start=启动后台任务（默认）",
                    "default": "start",
                },
                "command": {"type": "string", "description": "要执行的 shell 命令（action=start 必填）。"},
                "id": {"type": "string", "description": "任务编号（status / wait 用）。"},
                "timeout": {"type": "number", "description": "秒。start 默认 3600；wait 默认 30。"},
            },
            "required": [],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        action = args.get("action") or "start"
        if action == "start":
            return self.manager.start_confirmed(args, ctx=ctx, tool=self)
        if action == "status":
            info = self.manager.status(str(args.get("id") or ""))
            if info is None:
                return self.err("没有这个任务编号。")
            return self.ok(_fmt_task(info), structured=info)
        if action == "list":
            items = self.manager.list_tasks()
            if not items:
                return self.ok("当前没有后台任务。")
            body = "\n".join(_fmt_task(i) for i in items)
            return self.ok(f"{len(items)} 个后台任务：\n{body}", structured={"tasks": items})
        if action == "wait":
            info = self.manager.wait(
                str(args.get("id") or ""),
                timeout=float(args.get("timeout", 30.0) or 30.0),
            )
            if info is None:
                return self.err("没有这个任务编号。")
            note = "" if info["status"] != "running" else "（仍在运行，wait 超时返回）"
            return self.ok(f"{_fmt_task(info)}{note}", structured=info)
        return self.err(f"未知 action: {action}")


__all__ = ["BackgroundTaskTool"]
