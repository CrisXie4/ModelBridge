"""TodoTool — let the AI plan, track, and close its own work.

The tool is backed by a :class:`~modelbridge.agent.live_state.TodoStore`
shared with a :class:`~modelbridge.agent.live_state.LiveStateWriter`, so every
mutation immediately republishes ``live.json`` for the web UI.

Single ``action`` argument keeps the surface small while covering the full
lifecycle: ``add`` / ``update_status`` / ``remove`` / ``clear`` / ``list``.
"""

from __future__ import annotations

from typing import Any

from ..context import AgentContext
from ..live_state import TODO_PRIORITIES, TODO_STATUSES, TodoStore
from .base import Tool, ToolResult

_DONE_HINT = "完成后用 update_status 标记 done；进行中的标 in_progress。"


class TodoTool(Tool):
    name = "todo"
    description = (
        "管理本次会话的待办列表（计划 / 任务清单）。开始多步任务前先用 add 拆解步骤，"
        "执行中用 update_status 同步进度，让用户和 WebUI 实时看到你的计划与进展。"
        "单参数 action 决定操作类型。"
    )

    def __init__(self, store: TodoStore) -> None:
        self._store = store

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update_status", "remove", "clear", "list"],
                    "description": "要执行的操作。",
                },
                "content": {
                    "type": "string",
                    "description": "（add 必填）待办内容，一句话描述这个步骤要做什么。",
                },
                "priority": {
                    "type": "string",
                    "enum": TODO_PRIORITIES,
                    "description": "（add 可选）优先级，默认 normal。",
                },
                "todo_id": {
                    "type": "integer",
                    "description": "（update_status / remove 必填）目标待办的 id。",
                },
                "status": {
                    "type": "string",
                    "enum": TODO_STATUSES,
                    "description": "（update_status 必填）新状态：pending / in_progress / done。",
                },
                "only_status": {
                    "type": "string",
                    "enum": TODO_STATUSES,
                    "description": "（clear 可选）只清掉某种状态；不传则清空全部。",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        action = args.get("action")
        if action == "add":
            content = args.get("content")
            if not isinstance(content, str) or not content.strip():
                return self.err("add 需要 content 参数（待办内容）")
            priority = args.get("priority") or "normal"
            item = self._store.add(content.strip(), priority=str(priority))
            return self.ok(
                f"已添加待办 #{item.id}：{item.content}（{item.priority}）。\n{_DONE_HINT}",
                structured=item.to_dict(),
            )

        if action == "update_status":
            todo_id = args.get("todo_id")
            status = args.get("status")
            if not isinstance(todo_id, int):
                return self.err("update_status 需要 todo_id（整数）")
            if not isinstance(status, str) or status not in TODO_STATUSES:
                return self.err(
                    f"update_status 需要 status ∈ {list(TODO_STATUSES)}"
                )
            item = self._store.update_status(todo_id, status)
            if item is None:
                return self.err(f"找不到 id={todo_id} 的待办")
            return self.ok(
                f"已更新 #{item.id} → {item.status}：{item.content}",
                structured=item.to_dict(),
            )

        if action == "remove":
            todo_id = args.get("todo_id")
            if not isinstance(todo_id, int):
                return self.err("remove 需要 todo_id（整数）")
            ok = self._store.remove(todo_id)
            if not ok:
                return self.err(f"找不到 id={todo_id} 的待办")
            return self.ok(f"已删除待办 #{todo_id}")

        if action == "clear":
            only = args.get("only_status")
            if only is not None and only not in TODO_STATUSES:
                return self.err(f"only_status 必须 ∈ {list(TODO_STATUSES)}")
            n = self._store.clear(status=only if isinstance(only, str) else None)
            scope = f"状态为 {only} 的" if only else "所有"
            return self.ok(f"已清除 {scope}待办，共 {n} 条")

        if action == "list":
            items = self._store.to_list()
            s = self._store.summary()
            if not items:
                return self.ok("当前没有待办。多步任务建议先 add 拆解。")
            lines = [
                f"待办清单（{s['done']}/{s['total']} 完成，{s['in_progress']} 进行中）："
            ]
            for it in items:
                mark = {"done": "✓", "in_progress": "▸", "pending": "○"}.get(
                    it.status, "○"
                )
                lines.append(f"  {mark} #{it.id} [{it.status}/{it.priority}] {it.content}")
            return self.ok("\n".join(lines), structured={"items": items, "summary": s})

        return self.err(f"未知 action: {action}")

    def openai_tool(self) -> dict[str, Any]:
        """Override to inject the current snapshot into the description at wire time.

        The model sees its existing todos every turn — no separate list call
        needed to stay oriented.
        """
        tool = super().openai_tool()
        items = self._store.to_list()
        if items:
            s = self._store.summary()
            preview = "; ".join(
                f"#{it['id']} {it['status']}:{it['content']}" for it in items[:12]
            )
            extra = (
                f"\n\n当前清单（{s['done']}/{s['total']} 完成）：{preview}"
                + ("…" if len(items) > 12 else "")
            )
        else:
            extra = "\n\n当前清单为空。"
        tool["function"]["description"] = self.description + extra
        return tool


__all__ = ["TodoTool"]
