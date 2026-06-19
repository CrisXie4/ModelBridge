"""Skill discovery: parse SKILL.md frontmatter, scan global + project, skip malformed."""

from __future__ import annotations

from modelbridge.skills.discovery import discover_skills, find_skill, parse_skill


def _write_skill(root, name, description, body="做事的步骤。", scope_dir=".modelbridge/skills"):
    d = root / scope_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8"
    )
    return d / "SKILL.md"


def test_parse_valid_skill(tmp_path):
    md = _write_skill(tmp_path, "deploy", "部署到生产")
    sk = parse_skill(md, scope="project")
    assert sk is not None
    assert sk.name == "deploy"
    assert sk.description == "部署到生产"
    assert "做事的步骤" in sk.body
    assert sk.scope == "project"


def test_parse_malformed_returns_none(tmp_path):
    d = tmp_path / ".modelbridge" / "skills" / "bad"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
    assert parse_skill(d / "SKILL.md", scope="project") is None


def test_parse_missing_name_or_description_returns_none(tmp_path):
    d = tmp_path / ".modelbridge" / "skills" / "nodesc"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: nodesc\n---\nbody", encoding="utf-8")
    assert parse_skill(d / "SKILL.md", scope="project") is None


def test_discover_project_skills(tmp_path):
    _write_skill(tmp_path, "deploy", "部署")
    _write_skill(tmp_path, "review", "审查")
    skills = discover_skills(project_path=tmp_path)
    names = {s.name for s in skills}
    assert names == {"deploy", "review"}


def test_project_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MBRIDGE_HOME", str(home))
    # global skill "deploy" with one description
    _write_skill(home, "deploy", "GLOBAL 部署", scope_dir="skills")
    # project skill "deploy" with another description
    proj = tmp_path / "proj"
    _write_skill(proj, "deploy", "PROJECT 部署")
    skills = discover_skills(project_path=proj)
    deploy = find_skill("deploy", project_path=proj)
    assert deploy is not None
    assert deploy.description == "PROJECT 部署"  # project wins
    assert len([s for s in skills if s.name == "deploy"]) == 1


def test_find_skill_missing(tmp_path):
    assert find_skill("nope", project_path=tmp_path) is None
