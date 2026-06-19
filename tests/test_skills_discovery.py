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


def test_discover_skills_tolerates_oserror_on_iterdir(tmp_path, monkeypatch):
    """discover_skills() must not raise when d.iterdir() fails (FIX 1).

    We monkeypatch Path.iterdir to raise OSError for the skills root so that
    the guard is exercised.  A second, valid skill is placed where iterdir
    is NOT patched (project scope) so we can assert the rest of discovery
    still works after the bad directory is skipped.
    """
    # Write a valid project skill so we have something to find
    _write_skill(tmp_path, "good-skill", "正常工作的技能")

    # Also set up a fake global skills dir that iterdir will fail on
    home = tmp_path / "home"
    global_skills = home / "skills"
    global_skills.mkdir(parents=True)
    monkeypatch.setenv("MBRIDGE_HOME", str(home))

    original_iterdir = _orig_iterdir = tmp_path.__class__.iterdir

    def patched_iterdir(self):
        if self == global_skills:
            raise OSError("permission denied (simulated)")
        return original_iterdir(self)

    monkeypatch.setattr(tmp_path.__class__, "iterdir", patched_iterdir)

    # Must not raise; must still return the project skill
    skills = discover_skills(project_path=tmp_path)
    names = {s.name for s in skills}
    assert "good-skill" in names


def test_discover_skills_tolerates_oserror_on_sub_stat(tmp_path, monkeypatch):
    """discover_skills() must not raise when is_dir()/is_file() raises OSError
    for an individual entry inside a skills directory (FIX 1 inner guard)."""
    _write_skill(tmp_path, "real-skill", "真实技能")

    skills_dir = tmp_path / ".modelbridge" / "skills"

    # Create a dummy entry whose name sorts before "real-skill" so it is visited first
    bad_entry = skills_dir / "aaa-broken"
    bad_entry.mkdir(parents=True, exist_ok=True)

    original_is_dir = tmp_path.__class__.is_dir

    def patched_is_dir(self):
        if self == bad_entry:
            raise OSError("stat failed (simulated)")
        return original_is_dir(self)

    monkeypatch.setattr(tmp_path.__class__, "is_dir", patched_is_dir)

    # Must not raise; the broken entry is skipped, real-skill is found
    skills = discover_skills(project_path=tmp_path)
    names = {s.name for s in skills}
    assert "real-skill" in names
