"""wire_skills(): discovers skills, registers UseSkillTool, appends index to system prompt."""

from __future__ import annotations

from pathlib import Path

from modelbridge.agent.tools import ToolRegistry
from modelbridge.skills.wiring import wire_skills


def _write_skill(root: Path, name: str, description: str, body: str = "做事的步骤。") -> None:
    d = root / ".modelbridge" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_wire_skills_no_skills_returns_unchanged(tmp_path):
    """When no skills are found, the system prompt is returned unchanged and
    no new tool is registered on the registry."""
    registry = ToolRegistry()
    original = "You are a helpful assistant."
    result = wire_skills(registry, original, project_path=tmp_path)
    assert result == original
    assert "use_skill" not in registry.names()


def test_wire_skills_with_skills_registers_tool_and_appends_index(tmp_path):
    """When skills are present, use_skill is registered and the index is
    appended to the system prompt."""
    _write_skill(tmp_path, "deploy", "部署到生产")
    _write_skill(tmp_path, "review", "审查代码")
    registry = ToolRegistry()
    original = "You are a helpful assistant."
    result = wire_skills(registry, original, project_path=tmp_path)
    # Tool must be registered
    assert "use_skill" in registry.names()
    # Original prompt must still be present
    assert original in result
    # Index must be appended with skill names
    assert "deploy" in result
    assert "review" in result
    # Prompt changed (index appended)
    assert result != original
