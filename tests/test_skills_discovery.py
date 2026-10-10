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
    # Built-in skills are always present (lowest precedence); user/project
    # skills are merged on top.
    from modelbridge.skills.builtin import builtin_skill_names

    assert builtin_skill_names().issubset(names)
    assert {"deploy", "review"}.issubset(names)


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


# --- frontmatter BOM 剥离（Windows 记事本保存的 SKILL.md） -------------------

def test_parse_skill_strips_utf8_bom(tmp_path):
    """'\ufeff---' 不以 '---' 开头 —— BOM 不剥离时合法 SKILL.md 被静默跳过。"""
    d = tmp_path / ".modelbridge" / "skills" / "noted"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "\ufeff---\nname: noted\ndescription: 记事本保存的技能\n---\n步骤\n",
        encoding="utf-8",
    )
    sk = parse_skill(d / "SKILL.md", scope="project")
    assert sk is not None
    assert sk.name == "noted"
    assert sk.description == "记事本保存的技能"


def test_discover_skills_finds_bom_prefixed_skill(tmp_path):
    d = tmp_path / ".claude" / "skills" / "bom-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(
        "\ufeff---\nname: bom-skill\ndescription: 带 BOM 的技能\n---\n正文\n".encode("utf-8")
    )
    names = {s.name for s in discover_skills(project_path=tmp_path)}
    assert "bom-skill" in names


# --- 兼容根（.claude/skills、.agents/skills） --------------------------------

def test_discover_skills_from_claude_compat_root(tmp_path):
    _write_skill(tmp_path, "cc-deploy", "Claude 根里的部署", scope_dir=".claude/skills")
    names = {s.name for s in discover_skills(project_path=tmp_path)}
    assert "cc-deploy" in names


def test_discover_skills_from_agents_compat_root(tmp_path):
    _write_skill(tmp_path, "ag-review", "AGENTS 根里的审查", scope_dir=".agents/skills")
    names = {s.name for s in discover_skills(project_path=tmp_path)}
    assert "ag-review" in names


def test_native_root_wins_over_compat_roots(tmp_path):
    """同名 skill：原生 .modelbridge/skills 覆盖兼容根。"""
    _write_skill(tmp_path, "deploy", "COMPAT 部署", scope_dir=".claude/skills")
    _write_skill(tmp_path, "deploy", "AGENTS 部署", scope_dir=".agents/skills")
    _write_skill(tmp_path, "deploy", "NATIVE 部署")
    deploy = find_skill("deploy", project_path=tmp_path)
    assert deploy is not None
    assert deploy.description == "NATIVE 部署"


def test_project_compat_wins_over_global_native(tmp_path, monkeypatch):
    """跨作用域既有优先级不因兼容根而变：project 兼容根 > global 原生根。"""
    home = tmp_path / "home"
    monkeypatch.setenv("MBRIDGE_HOME", str(home))
    _write_skill(home, "deploy", "GLOBAL 部署", scope_dir="skills")
    _write_skill(tmp_path, "deploy", "PROJECT-CLAUDE 部署", scope_dir=".claude/skills")
    deploy = find_skill("deploy", project_path=tmp_path)
    assert deploy is not None
    assert deploy.description == "PROJECT-CLAUDE 部署"


def test_global_compat_roots_scanned(tmp_path, monkeypatch):
    """全局兼容根 ~/.claude/skills、~/.agents/skills 也参与发现。"""
    home = tmp_path / "home"
    monkeypatch.setenv("MBRIDGE_HOME", str(home))
    _write_skill(home, "g-cc", "全局 Claude", scope_dir=".claude/skills")
    _write_skill(home, "g-ag", "全局 AGENTS", scope_dir=".agents/skills")
    names = {s.name for s in discover_skills(project_path=tmp_path)}
    assert {"g-cc", "g-ag"}.issubset(names)
