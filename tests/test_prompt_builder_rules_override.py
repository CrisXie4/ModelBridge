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


# ---------------------------------------------------------------------------
# 常量环境信息块（cwd/platform/shell 进 stable prefix 的 project_summary 段）
# ---------------------------------------------------------------------------

import sys  # noqa: E402


def _bare_builder() -> PromptBuilder:
    """关闭磁盘规则加载，让断言只关心 project_summary 段本身。"""
    b = PromptBuilder()
    b.use_global_rules = False
    b.use_project_rules = False
    return b


def test_env_info_block_appended_to_project_summary(tmp_path: Path) -> None:
    r = (
        _bare_builder()
        .with_project(tmp_path)
        .with_project_summary("项目摘要")
        .build()
    )
    body = r.sections["project_summary"]
    assert "### 运行环境" in body
    assert f"`{tmp_path.resolve()}`" in body  # 工作目录（项目根）
    assert f"`{sys.platform}`" in body       # 平台
    assert "- Shell: `" in body              # 目标 shell（与 executor 语义一致）
    # 环境块随 stable prefix 一起进首条 system 消息。
    assert "### 运行环境" in r.messages[0].content


def test_env_info_block_absent_without_project() -> None:
    r = _bare_builder().with_project_summary("摘要").build()
    assert "### 运行环境" not in r.sections["project_summary"]


def test_env_info_block_bytes_stable_across_builds(tmp_path: Path) -> None:
    """同项目同机器：两次构建字节一致，prefix hash 不变（缓存照常命中）。"""
    a = (
        _bare_builder()
        .with_project(tmp_path)
        .with_project_summary("摘要")
        .build()
    )
    b = (
        _bare_builder()
        .with_project(tmp_path)
        .with_project_summary("摘要")
        .build()
    )
    assert a.sections["project_summary"] == b.sections["project_summary"]
    assert a.prompt_prefix_hash == b.prompt_prefix_hash


def test_env_info_block_differs_across_projects(tmp_path: Path) -> None:
    p1 = tmp_path / "alpha"
    p2 = tmp_path / "beta"
    p1.mkdir()
    p2.mkdir()
    ha = (
        _bare_builder().with_project(p1).with_project_summary("摘要").build()
    ).prompt_prefix_hash
    hb = (
        _bare_builder().with_project(p2).with_project_summary("摘要").build()
    ).prompt_prefix_hash
    assert ha != hb


# ---------------------------------------------------------------------------
# U11: 规则文件向上遍历至 git 仓库根（就近优先）
# ---------------------------------------------------------------------------

from modelbridge.project.scanner import find_git_root  # noqa: E402
from modelbridge.prompt.rules_loader import discover_rule_files  # noqa: E402


def _make_repo(base: Path) -> tuple[Path, Path]:
    """tmp/repo（.git 标记 + 根 AGENT.md）/ tmp/repo/sub/proj（自身 AGENT.md）。"""
    repo = base / "repo"
    proj = repo / "sub" / "proj"
    proj.mkdir(parents=True)
    (repo / ".git").mkdir()  # 目录形态的 git 标记
    (repo / "AGENT.md").write_text("仓库根规则\n", encoding="utf-8")
    (proj / "AGENT.md").write_text("子目录规则\n", encoding="utf-8")
    return repo, proj


def test_walk_to_git_root_collects_ancestors_nearest_first(tmp_path: Path) -> None:
    repo, proj = _make_repo(tmp_path)
    found = discover_rule_files(proj)
    project_files = [f for f in found if f.scope in ("project", "project_ancestor")]
    assert [f.scope for f in project_files] == ["project", "project_ancestor"]
    assert project_files[0].path == proj / "AGENT.md"       # 就近优先
    assert project_files[1].path == repo / "AGENT.md"
    assert project_files[1].label == "../../AGENT.md"  # proj 距 repo 两层，标签带相对层级


def test_walk_stops_at_git_root(tmp_path: Path) -> None:
    """绝不越出仓库边界：仓库外的规则文件不收集。"""
    (tmp_path / "AGENT.md").write_text("仓库外规则\n", encoding="utf-8")
    repo, proj = _make_repo(tmp_path)
    found = discover_rule_files(proj)
    assert all(f.path != tmp_path / "AGENT.md" for f in found)


def test_walk_disabled_by_param(tmp_path: Path) -> None:
    repo, proj = _make_repo(tmp_path)
    found = discover_rule_files(proj, walk_to_git_root=False)
    assert all(f.scope != "project_ancestor" for f in found)


def test_walk_disabled_by_config(tmp_path: Path, monkeypatch) -> None:
    import modelbridge.config as config_mod
    from modelbridge.models import AppConfig, PromptConfig

    monkeypatch.setattr(
        rl, "load_app_config",
        lambda: AppConfig(prompt=PromptConfig(rules_walk_to_git_root=False)),
    )
    monkeypatch.setattr(config_mod, "load_app_config", lambda: AppConfig())
    repo, proj = _make_repo(tmp_path)
    found = discover_rule_files(proj)  # walk_to_git_root=None → 读配置
    assert all(f.scope != "project_ancestor" for f in found)


def test_no_git_root_no_walk(tmp_path: Path) -> None:
    """无 git 标记 → 不向上遍历（保守单层行为）。"""
    proj = tmp_path / "plain" / "proj"
    proj.mkdir(parents=True)
    (tmp_path / "AGENT.md").write_text("上层规则\n", encoding="utf-8")
    (proj / "AGENT.md").write_text("子目录规则\n", encoding="utf-8")
    found = discover_rule_files(proj)
    assert all(f.scope != "project_ancestor" for f in found)


def test_git_marker_as_file_is_recognized(tmp_path: Path) -> None:
    """worktree 的 .git 是文件而非目录 —— 同样视为仓库标记。"""
    repo = tmp_path / "wt"
    proj = repo / "sub"
    proj.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: /somewhere\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("根规则\n", encoding="utf-8")
    assert find_git_root(proj) == repo
    found = discover_rule_files(proj)
    assert any(f.scope == "project_ancestor" for f in found)


def test_find_git_root_reaches_fs_root_without_marker(tmp_path: Path) -> None:
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    assert find_git_root(lonely) is None


def test_merged_rules_include_ancestor_content(fake_app_dir: Path, tmp_path: Path) -> None:
    """builder 装配时祖先规则进 project_rules 段，且子目录规则排在前（就近优先）。"""
    repo, proj = _make_repo(tmp_path)
    b = PromptBuilder().with_project(proj)
    b.use_global_rules = False
    r = b.build()
    body = r.sections["project_rules"]
    assert "子目录规则" in body and "仓库根规则" in body
    assert body.index("子目录规则") < body.index("仓库根规则")
