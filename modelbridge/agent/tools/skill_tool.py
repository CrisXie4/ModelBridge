"""UseSkillTool — load a user-defined skill by name after user confirmation.

The tool looks up the skill via :func:`modelbridge.skills.discovery.find_skill`,
asks the user for approval (``allow_always=True`` so one "always" click covers
all future skill invocations in the session), and returns the skill's body
on approval or an error result otherwise.

The project root is injected at construction time (via ``project_path``), NOT
asked of the model — the model has no knowledge of the filesystem layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...skills.discovery import find_skill
from ..context import AgentContext
from .base import Tool, ToolResult

_MAX_SKILL_CHARS = 16_000


class UseSkillTool(Tool):
    name = "use_skill"
    description = (
        "加载一个用户 skill 的完整指令到对话中（加载前会请求用户确认）。"
        "name 取自系统提示里的「可用 Skills」索引。"
    )

    def __init__(self, project_path: Path | str | None = None) -> None:
        self._project_path = project_path

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill 名称（与系统提示 skill 索引中的名称一致）。",
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

        skill = find_skill(skill_name, project_path=self._project_path)
        if skill is None:
            return self.err(
                f"未找到 skill: {skill_name}",
                hint="请用 `mbridge skill list` 查看可用 skill，或检查 SKILL.md 是否存在且格式正确。",
            )

        if not ctx.confirm(
            tool=self.name,
            summary=f"加载 skill「{skill_name}」",
            detail=f"描述: {skill.description}\n来源: {skill.path}",
            allow_always=True,
        ):
            return self.err(f"用户拒绝加载 skill: {skill_name}")

        body = skill.body
        if len(body) > _MAX_SKILL_CHARS:
            body = body[:_MAX_SKILL_CHARS] + "\n…[已截断]"

        return self.ok(
            f"# Skill: {skill.name}\n\n{body}",
            structured={"skill_name": skill.name, "scope": skill.scope, "path": str(skill.path)},
        )


__all__ = ["UseSkillTool"]
