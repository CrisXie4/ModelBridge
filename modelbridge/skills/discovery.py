"""Discover user skills (Claude-Code-compatible SKILL.md folders).

A skill is a directory ``<name>/SKILL.md`` whose file starts with a YAML
frontmatter block (``name`` + ``description``) followed by markdown
instructions. We scan, per scope, the compat roots first and the native
root last (native wins a name clash within the same scope; project beats
global — mirrors the rules-file discovery order):

* global:  ``~/.claude/skills/`` → ``~/.agents/skills/`` → ``~/.modelbridge/skills/``
* project: ``<project>/.claude/skills/`` → ``<project>/.agents/skills/``
           → ``<project>/.modelbridge/skills/``

The ``.claude`` / ``.agents`` compat roots let skills installed for other
agents in the ecosystem work out of the box, the same way the rules side
accepts AGENT.md / AGENTS.md / CLAUDE.md (see rules_loader).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..utils import get_app_dir, get_logger

#: UTF-8 BOM — 记事本等 Windows 编辑器保存时写入，剥掉后 frontmatter 才可辨。
_BOM = "\ufeff"


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    scope: str  # "global" | "project"
    # E7: 何时使用（frontmatter ``when_to_use``）—— 触发判断比“是什么”更
    # 能减少误触发；旧 SKILL.md 无该字段时为空串（行为不变）。
    when_to_use: str = ""


def parse_skill(skill_md: Path, *, scope: str) -> Skill | None:
    """Parse one SKILL.md. Returns None (caller skips) if frontmatter is
    missing/broken or lacks name/description — never raises."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Windows 记事本保存的文件带 UTF-8 BOM；"\ufeff---" 不以 "---" 开头，
    # 会让合法 SKILL.md 被当成无 frontmatter 而静默跳过（对齐 ZCode
    # extractFrontmatter 的 BOM 剥离）。
    text = text.removeprefix(_BOM)
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
    when_to_use = str(meta.get("when_to_use", "") or "").strip()
    if not name or not description:
        return None
    body = parts[2].lstrip("\n")
    return Skill(name=name, description=description, body=body, path=skill_md,
                 scope=scope, when_to_use=when_to_use)


#: 项目内 skill 兼容根目录（原生 ``.modelbridge`` 单独处理，见下）。
#: 兼容根先扫、原生根后扫 —— 同名冲突时原生根赢。
_COMPAT_SKILL_ROOT_DIRS: tuple[str, ...] = (".claude", ".agents")


def _skills_dirs(project_path: Path | str | None) -> list[tuple[Path, str]]:
    """Global first, then project, so project entries override global.

    每个作用域内兼容根（.claude/.agents）先扫、原生根后扫，因此同名
    skill 的优先级：project .modelbridge > project 兼容根 >
    global ~/.modelbridge/skills > global 兼容根。
    """
    dirs: list[tuple[Path, str]] = []
    app = get_app_dir()
    for sub in _COMPAT_SKILL_ROOT_DIRS:
        g = app / sub / "skills"
        if g.is_dir():
            dirs.append((g, "global"))
    g_native = app / "skills"
    if g_native.is_dir():
        dirs.append((g_native, "global"))
    if project_path is not None:
        base = Path(project_path)
        for sub in _COMPAT_SKILL_ROOT_DIRS:
            p = base / sub / "skills"
            if p.is_dir():
                dirs.append((p, "project"))
        p_native = base / ".modelbridge" / "skills"
        if p_native.is_dir():
            dirs.append((p_native, "project"))
    return dirs


def discover_skills(project_path: Path | str | None = None) -> list[Skill]:
    """Return all valid skills.

    Precedence (highest wins on name clash):
      project > global > builtin

    Built-ins are seeded first so any user/project skill of the same name
    silently overrides them.
    """
    found: dict[str, Skill] = {}

    # Built-ins (lowest precedence) — seeded first so later scopes overwrite.
    try:
        from .builtin import builtin_skills

        for sk in builtin_skills():
            found[sk.name] = sk
    except Exception:  # noqa: BLE001 — defensive: never break skill discovery
        get_logger().debug("skills: 内置 skill 加载失败，跳过", exc_info=True)

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
            parsed = parse_skill(md, scope=scope)
            if parsed is None:
                get_logger().warning("skills: 跳过无效 SKILL.md: %s", md)
                continue
            found[parsed.name] = parsed
    return list(found.values())


def find_skill(name: str, project_path: Path | str | None = None) -> Skill | None:
    name = (name or "").strip()
    for s in discover_skills(project_path):
        if s.name == name:
            return s
    return None


#: 技能索引预算与单条描述截断（E7，对齐 ZCode skills.ts 的
#: DEFAULT_SKILL_METADATA_BUDGET=20K 与 MAX_DESCRIPTION_CHARS=250）。
_SKILL_INDEX_BUDGET = 20_000
_SKILL_MAX_DESC_CHARS = 250


def build_skills_index(skills: list[Skill], *, budget: int = _SKILL_INDEX_BUDGET) -> str:
    """Compact index injected into the system prompt. '' when no skills.

    E7: 单条 description / when_to_use 截断（≤250 字符）；``when_to_use``
    附加在索引行内；总长超预算时降级为“名字+路径”清单（宁丢描述不丢
    可发现性，对齐 ZCode buildSkillsContent 的 names-only 兜底）。
    """
    if not skills:
        return ""
    header = [
        "# 可用 Skills",
        "",
        (
            "以下是可用的 skill。判断与当前任务相关时，调用 "
            '`use_skill("<name>")` 加载其完整指令。'
            "不要凭名字猜测 skill 的内容。"
        ),
        "",
    ]

    def _clip(v: str) -> str:
        return v if len(v) <= _SKILL_MAX_DESC_CHARS else v[:_SKILL_MAX_DESC_CHARS] + "…"

    def _full_line(s: Skill) -> str:
        tag = " [内置·免确认]" if s.scope == "builtin" else ""
        line = f"- {s.name}: {_clip(s.description)}"
        if s.when_to_use:
            line += f"（何时用：{_clip(s.when_to_use)}）"
        return f"{line}{tag}"

    full = "\n".join(header + [_full_line(s) for s in skills]) + "\n"
    if len(full) <= budget:
        return full
    names_only = "\n".join(
        header + [f"- {s.name} (file: {s.path})" for s in skills]
    ) + "\n"
    return names_only
