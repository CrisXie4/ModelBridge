"""UseSkillTool — load a user-defined skill by name after user confirmation.

The tool looks up the skill via :func:`modelbridge.skills.discovery.find_skill`,
asks the user for approval (``allow_always=True`` so one "always" click covers
all future skill invocations in the session), and returns the skill's body
on approval or an error result otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...skills.discovery import find_skill
from ..context import AgentContext
from .base import Tool, ToolResult


class UseSkillTool(Tool):
    name = "use_skill"
    description = (
        "加载并执行一个用户定义的 skill（skill 名 + 可选项目路径）。"
        "执行前会请求用户确认；批准后返回 skill 的完整指令正文。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill 名称（与 SKILL.md 中 name 字段一致）。",
                },
                "project_path": {
                    "type": "string",
                    "description": "项目根目录路径（可选；不传时只查全局 skills）。",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        skill_name = args.get("name")
        if not isinstance(skill_name, str) or not skill_name.strip():
            return self.err("缺少必填参数 name")

        skill_name = skill_name.strip()

        raw_project_path = args.get("project_path")
        if raw_project_path is not None:
            project_path: Path | None = Path(raw_project_path)
        else:
            project_path = None

        skill = find_skill(skill_name, project_path=project_path)
        if skill is None:
            return self.err(
                f"未找到 skill: {skill_name}",
                hint="请用 `list_skills` 查看可用 skill，或检查 SKILL.md 是否存在且格式正确。",
            )

        if not ctx.confirm(
            tool=self.name,
            summary=f"加载 skill「{skill_name}」",
            detail=f"描述: {skill.description}\n来源: {skill.path}",
            allow_always=True,
        ):
            return self.err(f"用户拒绝加载 skill: {skill_name}")

        return self.ok(
            skill.body,
            structured={"skill_name": skill.name, "scope": skill.scope, "path": str(skill.path)},
        )


__all__ = ["UseSkillTool"]
