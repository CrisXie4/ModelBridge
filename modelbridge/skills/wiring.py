"""wire_skills: discover user skills, register UseSkillTool, append index to system prompt."""

from __future__ import annotations

from pathlib import Path

from ..agent.tools import ToolRegistry
from ..agent.tools.skill_tool import UseSkillTool
from .discovery import build_skills_index, discover_skills


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
