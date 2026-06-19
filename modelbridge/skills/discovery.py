"""Discover user skills (Claude-Code-compatible SKILL.md folders).

A skill is a directory ``<name>/SKILL.md`` whose file starts with a YAML
frontmatter block (``name`` + ``description``) followed by markdown
instructions. We scan two roots — the global ``~/.modelbridge/skills/`` and
the project ``<project>/.modelbridge/skills/`` — with project winning on a
name clash (mirrors the rules-file discovery order).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..utils import get_app_dir, get_logger


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    scope: str  # "global" | "project"


def parse_skill(skill_md: Path, *, scope: str) -> Skill | None:
    """Parse one SKILL.md. Returns None (caller skips) if frontmatter is
    missing/broken or lacks name/description — never raises."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.lstrip().startswith("---"):
        return None
    # Split: ['', <frontmatter>, <body>]
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name", "") or "").strip()
    description = str(meta.get("description", "") or "").strip()
    if not name or not description:
        return None
    body = parts[2].lstrip("\n")
    return Skill(name=name, description=description, body=body, path=skill_md, scope=scope)


def _skills_dirs(project_path: Path | str | None) -> list[tuple[Path, str]]:
    """Global first, then project, so project entries override global."""
    dirs: list[tuple[Path, str]] = []
    g = get_app_dir() / "skills"
    if g.is_dir():
        dirs.append((g, "global"))
    if project_path is not None:
        p = Path(project_path) / ".modelbridge" / "skills"
        if p.is_dir():
            dirs.append((p, "project"))
    return dirs


def discover_skills(project_path: Path | str | None = None) -> list[Skill]:
    """Return all valid skills (project overrides global by name)."""
    found: dict[str, Skill] = {}
    for d, scope in _skills_dirs(project_path):
        try:
            subdirs = sorted(d.iterdir())
        except OSError:
            get_logger().warning("skills: 无法读取目录 %s", d)
            continue
        for sub in subdirs:
            try:
                if not sub.is_dir():
                    continue
                md = sub / "SKILL.md"
                if not md.is_file():
                    continue
            except OSError:
                continue
            sk = parse_skill(md, scope=scope)
            if sk is None:
                get_logger().warning("skills: 跳过无效 SKILL.md: %s", md)
                continue
            found[sk.name] = sk
    return list(found.values())


def find_skill(name: str, project_path: Path | str | None = None) -> Skill | None:
    name = (name or "").strip()
    for s in discover_skills(project_path):
        if s.name == name:
            return s
    return None


def build_skills_index(skills: list[Skill]) -> str:
    """Compact index injected into the system prompt. '' when no skills."""
    if not skills:
        return ""
    lines = [
        "# 可用 Skills",
        "",
        "以下是用户提供的 skill。判断与当前任务相关时，调用 "
        '`use_skill("<name>")` 加载其完整指令（会请求用户确认）。'
        "不要凭名字猜测 skill 的内容。",
        "",
    ]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}")
    return "\n".join(lines) + "\n"
