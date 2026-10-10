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


# --- E7: 索引预算截断 + when_to_use ------------------------------------------

def test_when_to_use_appears_in_index_line():
    skills = [_make_skill("deploy", "部署到生产")]
    skills[0].when_to_use = "用户要求发布/上线时"
    result = build_skills_index(skills)
    assert "何时用：用户要求发布/上线时" in result


def test_long_description_is_clipped_per_entry():
    skills = [_make_skill("chatty", "描" * 400)]
    result = build_skills_index(skills)
    assert ("描" * 250 + "…") in result
    assert ("描" * 251) not in result


def test_when_to_use_also_clipped():
    skills = [_make_skill("chatty", "短描述")]
    skills[0].when_to_use = "何" * 400
    result = build_skills_index(skills)
    assert ("何" * 250 + "…") in result
    assert ("何" * 251) not in result


def test_budget_overflow_degrades_to_names_and_paths(tmp_path):
    """总长超预算 → 降级“名字+路径”清单（描述省略但可发现性保留）。"""
    skills = [
        _make_skill(f"skill-{i}", "细节" * 500) for i in range(30)
    ]
    for i, s in enumerate(skills):
        s.path = tmp_path / f"s{i}" / "SKILL.md"
    # 单条已截到 250 字符 → 30 条全量约 8K；budget 5K 时超限触发降级，
    # 降级清单只剩头部+名字/路径（≈4K，Windows 长路径使其自身也有数 K）。
    result = build_skills_index(skills, budget=5_000)
    assert len(result) < 5_000
    assert "细节" * 500 not in result  # 长描述不进降级清单
    for i in range(30):
        assert f"skill-{i}" in result
        assert f"(file: {tmp_path / f's{i}' / 'SKILL.md'})" in result


def test_default_budget_holds_for_normal_catalogs():
    """常规规模（30 条 × 500 字符描述 ≈ 15K+头部）仍在 20K 预算内，不降级。"""
    skills = [_make_skill(f"s-{i}", "描" * 500) for i in range(30)]
    result = build_skills_index(skills)
    assert "描" * 100 in result  # 全量模式（含描述）
    assert "(file:" not in result
