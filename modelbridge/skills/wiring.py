"""wire_skills: discover user skills, register UseSkillTool, append index to system prompt."""

from __future__ import annotations

from pathlib import Path

from ..agent.tools import ToolRegistry
from ..agent.tools.skill_tool import UseSkillTool
from .discovery import build_skills_index, discover_skills
from .prefs import load_disabled


def wire_skills(
    registry: ToolRegistry,
    system_prompt: str,
    *,
    project_path: Path | str | None,
) -> str:
    """Discover skills; if any, register UseSkillTool and append the index.

    Skills disabled via ``~/.modelbridge/skills.json`` (``/skill off <name>``)
    are filtered out before the index is built AND from the UseSkillTool's
    callable surface — a disabled skill can neither be listed to the model nor
    invoked.

    Args:
        registry: The agent tool registry to register UseSkillTool into.
        system_prompt: The current system prompt text.
        project_path: Project root for project-scoped skill discovery.

    Returns:
        The (possibly extended) system prompt. Returns *system_prompt* unchanged
        when no skills are found.
    """
    disabled = load_disabled()
    skills = [s for s in discover_skills(project_path=project_path)
              if s.name not in disabled]
    if not skills:
        return system_prompt

    registry.register(UseSkillTool(project_path=project_path, disabled=disabled))

    index = build_skills_index(skills)
    return system_prompt + "\n\n" + index
