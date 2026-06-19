"""build_skills_index: compact prompt index for available skills."""

from __future__ import annotations

from pathlib import Path

from modelbridge.skills.discovery import Skill, build_skills_index


def _make_skill(name: str, description: str) -> Skill:
    return Skill(name=name, description=description, body="", path=Path("/fake"), scope="project")


def test_empty_skills_returns_empty_string():
    assert build_skills_index([]) == ""


def test_index_lists_each_skill_and_mentions_use_skill():
    skills = [
        _make_skill("deploy", "部署到生产"),
        _make_skill("review", "审查代码"),
    ]
    result = build_skills_index(skills)
    # Each skill name and description must appear
    assert "deploy" in result
    assert "部署到生产" in result
    assert "review" in result
    assert "审查代码" in result
    # Must point to use_skill
    assert "use_skill" in result
