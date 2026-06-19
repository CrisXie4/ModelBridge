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
    # project_path injected via constructor — NOT passed in args
    result = UseSkillTool(project_path=tmp_path).execute({"name": "deploy"}, ctx)
    assert not result.is_error
    assert "构建" in result.content
    assert "发布" in result.content


def test_denied_returns_error(tmp_path):
    _write_skill(tmp_path, "deploy", "部署到生产", body="步骤。")
    ctx = _ctx(auto_no, tmp_path)
    result = UseSkillTool(project_path=tmp_path).execute({"name": "deploy"}, ctx)
    assert result.is_error


def test_unknown_skill_returns_error(tmp_path):
    ctx = _ctx(auto_yes, tmp_path)
    result = UseSkillTool(project_path=tmp_path).execute({"name": "nonexistent"}, ctx)
    assert result.is_error
    assert "nonexistent" in result.content


def test_missing_name_returns_error(tmp_path):
    ctx = _ctx(auto_yes, tmp_path)
    result = UseSkillTool(project_path=tmp_path).execute({}, ctx)
    assert result.is_error


def test_project_scoped_skill_found_via_constructor(tmp_path):
    """A skill in <project>/.modelbridge/skills/ is found when project_path
    is injected at construction — not passed in args (which the model cannot do)."""
    _write_skill(tmp_path, "release", "发布流程", body="打标签然后推送。")
    ctx = _ctx(auto_yes, tmp_path)
    # The model only provides name; project_path comes from constructor
    result = UseSkillTool(project_path=tmp_path).execute({"name": "release"}, ctx)
    assert not result.is_error
    assert "打标签" in result.content


def test_no_project_path_misses_project_skill(tmp_path):
    """Without constructor-injected project_path, a project-scoped skill is NOT found."""
    _write_skill(tmp_path, "release", "发布流程", body="打标签然后推送。")
    ctx = _ctx(auto_yes, tmp_path)
    # UseSkillTool() without project_path → only global skills searched
    result = UseSkillTool().execute({"name": "release"}, ctx)
    assert result.is_error
