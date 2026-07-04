"""Tests for ``mbridge skill`` CLI subcommands.

Covers:
  1. ``mbridge skill list`` — shows installed skills
  2. ``mbridge skill show <name>`` — shows skill body
  3. ``mbridge skill add <path>`` — warns loudly, requires confirmation; copies skill
  4. ``mbridge skill remove <name>`` — deletes skill from global dir
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from modelbridge.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_dir(root: Path, name: str, description: str = "测试技能") -> Path:
    """Create a minimal skill folder at ``root/<name>/SKILL.md``."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n技能说明。\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated MBRIDGE_HOME with init applied."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


@pytest.fixture()
def home_with_skill(home: Path):
    """MBRIDGE_HOME with one pre-installed global skill ('deploy')."""
    skills_dir = home / "skills"
    skills_dir.mkdir(exist_ok=True)
    _make_skill_dir(skills_dir, "deploy", "部署到生产")
    return home


# ---------------------------------------------------------------------------
# Test 1: skill list
# ---------------------------------------------------------------------------


def test_skill_list_shows_installed(home_with_skill: Path):
    """``mbridge skill list`` should show the installed skill name and description."""
    r = runner.invoke(app, ["skill", "list"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "deploy" in r.output
    assert "部署到生产" in r.output


# ---------------------------------------------------------------------------
# Test 2: skill show
# ---------------------------------------------------------------------------


def test_skill_show_prints_body(home_with_skill: Path):
    """``mbridge skill show deploy`` should print the skill body text."""
    r = runner.invoke(app, ["skill", "show", "deploy"])
    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    assert "技能说明" in r.output


# ---------------------------------------------------------------------------
# Test 3: skill add (confirm → copies)
# ---------------------------------------------------------------------------


def test_skill_add_warns_and_copies(tmp_path: Path, home: Path):
    """``mbridge skill add <path>`` should print a security warning and, on
    confirmation, copy the skill folder into ``~/.modelbridge/skills/``."""
    src_dir = tmp_path / "src_skills"
    skill_src = _make_skill_dir(src_dir, "myskill", "我的技能")

    # Feed "y\n" as confirmation input
    r = runner.invoke(app, ["skill", "add", str(skill_src)], input="y\n")

    assert r.exit_code == 0, f"exit_code={r.exit_code}\n{r.output}"
    # Security warning must be visible
    assert "警告" in r.output or "WARNING" in r.output or "安全" in r.output or "危险" in r.output
    # Skill should now exist in the global skills dir
    installed = home / "skills" / "myskill" / "SKILL.md"
    assert installed.exists(), (
        f"Expected skill to be copied to {installed}\noutput:\n{r.output}"
    )


def test_skill_add_aborts_on_decline(tmp_path: Path, home: Path):
    """Declining the confirmation should NOT copy the skill."""
    src_dir = tmp_path / "src_skills"
    skill_src = _make_skill_dir(src_dir, "badskill", "坏技能")

    r = runner.invoke(app, ["skill", "add", str(skill_src)], input="n\n")

    # Should exit cleanly (0 or non-zero abort) but NOT copy the skill
    installed = home / "skills" / "badskill" / "SKILL.md"
    assert not installed.exists(), (
        f"Skill should NOT have been copied on decline\noutput:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# Test 4: skill remove
# ---------------------------------------------------------------------------


def test_skill_remove_deletes_skill(home_with_skill: Path):
    """`mbridge skill remove` was PHYSICALLY REMOVED in v1.2 (R2a cleanup).

    Skills now live entirely under the user's responsibility — remove by
    deleting `~/.modelbridge/skills/<name>/` directly. Guard: `skill remove`
    must NOT resolve.
    """
    assert (home_with_skill / "skills" / "deploy").exists()

    r = runner.invoke(app, ["skill", "remove", "deploy"])
    assert r.exit_code == 2, (
        f"`mbridge skill remove` should be unknown (exit_code=2) after v1.2, "
        f"got exit_code={r.exit_code}\n{r.output}"
    )
    assert "no such command" in r.output.lower(), (
        f"Expected 'No such command' after v1.2 cleanup, got:\n{r.output}"
    )
    # And the skill dir is obviously untouched (nothing ran).
    assert (home_with_skill / "skills" / "deploy").exists(), (
        f"Skill dir should still exist (no command ran)\noutput:\n{r.output}"
    )
