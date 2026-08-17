"""Tests for the core_system override logic in PromptBuilder.

Three branches:
  ① user system.md differs from default -> use user system.md
  ② no real system.md but user wrote any rule file -> core_system suppressed
  ③ fresh install (nothing) -> built-in DEFAULT_SYSTEM_MD
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modelbridge.prompt import PromptBuilder
from modelbridge.prompt import rules_loader as rl
from modelbridge.prompt.defaults import DEFAULT_SYSTEM_MD


@pytest.fixture
def fake_app_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect ~/.modelbridge to a tmp dir."""
    app = tmp_path / "appdir"
    app.mkdir()
    monkeypatch.setattr(rl, "get_app_dir", lambda: app)
    return app


def test_branch1_user_system_md_takes_over(fake_app_dir: Path) -> None:
    # user wrote a system.md that is NOT the default
    (fake_app_dir / "system.md").write_text(
        "# 我的系统提示\n你是一个 Rust 专家。", encoding="utf-8"
    )
    r = PromptBuilder().build()
    assert "Rust 专家" in r.sections["core_system"]
    assert "ModelBridge System Prompt" not in r.sections["core_system"]
    assert r.sources["core_system"] == ["~/.modelbridge/system.md (user)"]


def test_branch2_user_rules_suppress_default(fake_app_dir: Path, tmp_path: Path) -> None:
    # no system.md; user wrote an AGENT.md in the project
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENT.md").write_text("# 项目规则\n用 Rust 写。", encoding="utf-8")
    r = PromptBuilder().with_project(project).build()
    assert r.sections["core_system"] == ""
    assert r.sources["core_system"] == ["<suppressed: user rules take over>"]


def test_branch2_global_rules_md_also_suppresses(fake_app_dir: Path) -> None:
    # no system.md, no project; just user-global rules.md
    (fake_app_dir / "rules.md").write_text("- 永远用中文", encoding="utf-8")
    r = PromptBuilder().build()
    assert r.sections["core_system"] == ""
    assert r.sources["core_system"] == ["<suppressed: user rules take over>"]


def test_branch2_empty_rule_file_does_not_suppress(fake_app_dir: Path, tmp_path: Path) -> None:
    # AGENT.md exists but is empty/whitespace, no system.md -> default injected
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENT.md").write_text("   \n\n  ", encoding="utf-8")
    r = PromptBuilder().with_project(project).build()
    assert "ModelBridge System Prompt" in r.sections["core_system"]
    assert r.sources["core_system"] == ["<built-in default>"]


def test_branch2_empty_rule_file_with_default_system_md(
    fake_app_dir: Path, tmp_path: Path
) -> None:
    # empty AGENT.md + system.md equal to default -> "default" path
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENT.md").write_text("   \n\n  ", encoding="utf-8")
    (fake_app_dir / "system.md").write_text(DEFAULT_SYSTEM_MD, encoding="utf-8")
    r = PromptBuilder().with_project(project).build()
    assert r.sources["core_system"] == ["~/.modelbridge/system.md (default)"]


def test_branch3_fresh_install_uses_default(fake_app_dir: Path) -> None:
    r = PromptBuilder().build()
    assert r.sections["core_system"].strip() == DEFAULT_SYSTEM_MD.strip()
    assert r.sources["core_system"] == ["<built-in default>"]


def test_branch1_default_system_md_treated_as_default(fake_app_dir: Path) -> None:
    # system.md exists but equals the default verbatim -> still "default" path
    (fake_app_dir / "system.md").write_text(DEFAULT_SYSTEM_MD, encoding="utf-8")
    r = PromptBuilder().build()
    assert r.sources["core_system"] == ["~/.modelbridge/system.md (default)"]
    assert "ModelBridge System Prompt" in r.sections["core_system"]
