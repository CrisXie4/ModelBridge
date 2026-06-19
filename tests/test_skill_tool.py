"""UseSkillTool: approved→body, denied→error, unknown→error, missing name→error."""

from __future__ import annotations

from pathlib import Path

from modelbridge.agent.context import AgentContext, auto_no, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.skill_tool import UseSkillTool


def _write_skill(root: Path, name: str, description: str, body: str = "步骤内容。") -> None:
    d = root / ".modelbridge" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def _ctx(approve, project_path: Path) -> AgentContext:
    return AgentContext(
        policy=PathPolicy(allowed_dirs=[], blocked_patterns=[]),
        cwd=project_path,
        approve=approve,
    )


def test_approved_returns_body(tmp_path):
    _write_skill(tmp_path, "deploy", "部署到生产", body="1. 构建\n2. 发布\n")
    ctx = _ctx(auto_yes, tmp_path)
    result = UseSkillTool().execute({"name": "deploy", "project_path": str(tmp_path)}, ctx)
    assert not result.is_error
    assert "构建" in result.content
    assert "发布" in result.content


def test_denied_returns_error(tmp_path):
    _write_skill(tmp_path, "deploy", "部署到生产", body="步骤。")
    ctx = _ctx(auto_no, tmp_path)
    result = UseSkillTool().execute({"name": "deploy", "project_path": str(tmp_path)}, ctx)
    assert result.is_error


def test_unknown_skill_returns_error(tmp_path):
    ctx = _ctx(auto_yes, tmp_path)
    result = UseSkillTool().execute({"name": "nonexistent", "project_path": str(tmp_path)}, ctx)
    assert result.is_error
    assert "nonexistent" in result.content


def test_missing_name_returns_error(tmp_path):
    ctx = _ctx(auto_yes, tmp_path)
    result = UseSkillTool().execute({"project_path": str(tmp_path)}, ctx)
    assert result.is_error
