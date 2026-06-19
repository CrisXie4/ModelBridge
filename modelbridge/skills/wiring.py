"""wire_skills: discover user skills, register UseSkillTool, append index to system prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agent.context import AgentContext
from ..agent.tools import ToolRegistry
from ..agent.tools.base import Tool, ToolResult
from .discovery import build_skills_index, discover_skills, find_skill


class UseSkillTool(Tool):
    """Load the full instructions of a named skill into the context.

    The tool returns the complete SKILL.md body so the model can follow
    the user-written instructions. Before doing so it asks the user for
    confirmation (ctx.confirm), mirroring the write-tool approval pattern.
    """

    name = "use_skill"
    description = (
        "加载指定 skill 的完整指令（SKILL.md 正文）到当前上下文。"
        "仅在系统提示的 skill 索引中有相应条目时才应调用此工具。"
        "调用前需用户确认。"
    )

    def __init__(self, project_path: Path | str | None = None) -> None:
        self._project_path = project_path

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要加载的 skill 名称，必须与系统提示 skill 索引中的名称完全一致。",
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
                f"找不到 skill {skill_name!r}。"
                "请检查系统提示中的 skill 索引，确认名称拼写。"
            )

        approved = ctx.confirm(
            tool=self.name,
            summary=f"加载 skill: {skill_name}",
            detail=f"来源: {skill.path}  ({skill.scope})",
        )
        if not approved:
            return self.err(f"用户拒绝加载 skill {skill_name!r}。")

        return self.ok(
            f"# Skill: {skill.name}\n\n{skill.body}",
            structured={"skill_name": skill.name, "scope": skill.scope, "path": str(skill.path)},
        )


def wire_skills(
    registry: ToolRegistry,
    system_prompt: str,
    *,
    project_path: Path | str | None,
) -> str:
    """Discover skills; if any, register UseSkillTool and append the index.

    Args:
        registry: The agent tool registry to register UseSkillTool into.
        system_prompt: The current system prompt text.
        project_path: Project root for project-scoped skill discovery.

    Returns:
        The (possibly extended) system prompt. Returns *system_prompt* unchanged
        when no skills are found.
    """
    skills = discover_skills(project_path=project_path)
    if not skills:
        return system_prompt

    registry.register(UseSkillTool(project_path=project_path))

    index = build_skills_index(skills)
    return system_prompt + "\n\n" + index
