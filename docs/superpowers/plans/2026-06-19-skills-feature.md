# ModelBridge Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the mbridge REPL agent discover user-provided skills (Claude-Code-compatible `SKILL.md` folders), see a compact index in its system prompt, and load a skill's full instructions on demand via a `use_skill` tool that requires user confirmation — plus a `mbridge skill` CLI group with a security warning on add.

**Architecture:** A new `modelbridge/skills/` package handles discovery (scan `~/.modelbridge/skills/` + project `.modelbridge/skills/`, parse YAML frontmatter) and builds the index text. A new `UseSkillTool` (registered in the agent ToolRegistry) loads a skill's body after `ctx.confirm`. The REPL wiring appends the index to the system prompt and registers the tool when skills exist. A `mbridge skill` Typer group manages skills, warning loudly on `add`.

**Tech Stack:** Python 3.11, `yaml` (PyYAML, already a dep), Typer 0.25.1, `rich`, `pytest`.

**Environment note:** Test with `py -3.11 -m pytest <path> -v`; lint `py -3.11 -m ruff check .`. Branch: `main` (or a feature branch — controller decides). Skills dirs resolve via `get_app_dir()` from `modelbridge.utils` which respects `MBRIDGE_HOME` (tests sandbox with `monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))`).

---

### Task 1: Skill discovery + parsing

**Files:**
- Create: `modelbridge/skills/__init__.py`
- Create: `modelbridge/skills/discovery.py`
- Test: `tests/test_skills_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skills_discovery.py
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
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_skills_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: modelbridge.skills.discovery`.

- [ ] **Step 3: Implement discovery**

Create `modelbridge/skills/__init__.py`:

```python
"""User-provided skills: discovery, indexing, and the use_skill tool wiring."""

from .discovery import Skill, build_skills_index, discover_skills, find_skill, parse_skill

__all__ = ["Skill", "build_skills_index", "discover_skills", "find_skill", "parse_skill"]
```

Create `modelbridge/skills/discovery.py`:

```python
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
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            md = sub / "SKILL.md"
            if not md.is_file():
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
```

NOTE: confirm `get_logger` is exported from `modelbridge.utils` (grep `def get_logger` in `modelbridge/utils.py`; `rules_loader` / others use it). If it's named differently, use the real name.

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_skills_discovery.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add modelbridge/skills/__init__.py modelbridge/skills/discovery.py tests/test_skills_discovery.py
git commit -m "feat(skills): discover Claude-Code-compatible SKILL.md folders (global + project)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Skills index builder test

**Files:**
- Test: `tests/test_skills_index.py` (the `build_skills_index` fn already lives in discovery.py from Task 1)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skills_index.py
"""build_skills_index produces a compact, use_skill-pointing index (or '' when empty)."""

from __future__ import annotations

from pathlib import Path

from modelbridge.skills.discovery import Skill, build_skills_index


def _sk(name, desc):
    return Skill(name=name, description=desc, body="...", path=Path("x"), scope="project")


def test_empty_index_is_blank():
    assert build_skills_index([]) == ""


def test_index_lists_each_skill_and_points_to_use_skill():
    out = build_skills_index([_sk("deploy", "部署到生产"), _sk("review", "代码审查")])
    assert "use_skill" in out
    assert "- deploy: 部署到生产" in out
    assert "- review: 代码审查" in out
```

- [ ] **Step 2: Run and verify it passes immediately** (the fn exists from Task 1)

Run: `py -3.11 -m pytest tests/test_skills_index.py -v`
Expected: PASS (2 tests). (If `build_skills_index` is missing/renamed, fix Task 1's implementation, not the test.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_skills_index.py
git commit -m "test(skills): cover build_skills_index format and empty case

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `use_skill` tool

**Files:**
- Create: `modelbridge/agent/tools/skill_tool.py`
- Test: `tests/test_skill_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_tool.py
"""use_skill tool: confirm→load body; deny→error; unknown name→error."""

from __future__ import annotations

from modelbridge.agent.context import AgentContext, auto_no, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.skill_tool import UseSkillTool


def _ctx(tmp_path, approve):
    policy = PathPolicy(allowed_dirs=[tmp_path.resolve()], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path.resolve(), approve=approve)


def _write_skill(proj, name, desc, body):
    d = proj / ".modelbridge" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n", encoding="utf-8")


def test_use_skill_loads_body_when_approved(tmp_path):
    _write_skill(tmp_path, "deploy", "部署", "第一步: 构建。第二步: 上线。")
    res = UseSkillTool(project_path=tmp_path).execute({"name": "deploy"}, _ctx(tmp_path, auto_yes))
    assert not res.is_error
    assert "第一步: 构建" in res.content


def test_use_skill_denied_returns_error(tmp_path):
    _write_skill(tmp_path, "deploy", "部署", "body")
    res = UseSkillTool(project_path=tmp_path).execute({"name": "deploy"}, _ctx(tmp_path, auto_no))
    assert res.is_error
    assert "拒绝" in res.content


def test_use_skill_unknown_name_returns_error(tmp_path):
    res = UseSkillTool(project_path=tmp_path).execute({"name": "nope"}, _ctx(tmp_path, auto_yes))
    assert res.is_error
    assert "未找到" in res.content


def test_use_skill_missing_name_returns_error(tmp_path):
    res = UseSkillTool(project_path=tmp_path).execute({}, _ctx(tmp_path, auto_yes))
    assert res.is_error
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_skill_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: modelbridge.agent.tools.skill_tool`.

- [ ] **Step 3: Implement the tool**

Create `modelbridge/agent/tools/skill_tool.py`:

```python
"""use_skill — load a user skill's full instructions into the conversation.

The model sees a compact skill index in the system prompt and calls this tool
when a skill is relevant. Because a skill is arbitrary user-provided text that
the model will then follow, loading goes through ``ctx.confirm`` (the same
y/N/always approval as write/bash) so a malicious skill can't enter the
conversation without the user's explicit OK.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...skills.discovery import find_skill
from ..context import AgentContext
from .base import Tool, ToolResult

_MAX_SKILL_CHARS = 16000


class UseSkillTool(Tool):
    name = "use_skill"
    description = (
        "加载一个用户 skill 的完整指令到对话中（加载前会请求用户确认）。"
        "name 取自系统提示里的「可用 Skills」索引。"
    )

    def __init__(self, project_path: Path | str | None = None) -> None:
        self._project_path = project_path

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要加载的 skill 名（见 skill 索引）。"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            return self.err("缺少必填参数 name")
        sk = find_skill(name, self._project_path)
        if sk is None:
            return self.err(f"未找到 skill '{name}'", hint="用 `mbridge skill list` 看可用 skill。")
        approved = ctx.confirm(
            tool="use_skill",
            summary=f"加载 skill «{sk.name}»",
            detail=(
                f"来源: {sk.path}\n"
                "⚠ 该 skill 的指令将进入对话并被 AI 执行，请确认你信任它。"
            ),
            group=f"use_skill:{sk.name}",
            allow_always=True,
        )
        if not approved:
            return self.err(f"用户拒绝加载 skill '{name}'")
        body = sk.body
        if len(body) > _MAX_SKILL_CHARS:
            body = body[:_MAX_SKILL_CHARS] + f"\n…[已截断，共 {len(sk.body)} 字符]"
        return self.ok(
            f"# Skill: {sk.name}\n\n{body}",
            structured={"skill": sk.name, "path": str(sk.path), "scope": sk.scope},
        )


__all__ = ["UseSkillTool"]
```

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_skill_tool.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add modelbridge/agent/tools/skill_tool.py tests/test_skill_tool.py
git commit -m "feat(skills): use_skill tool — load a skill body after user confirmation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: REPL wiring helper

**Files:**
- Create: `modelbridge/skills/wiring.py`
- Modify: `modelbridge/cli.py` (the REPL setup — register the tool + append the index)
- Modify: `modelbridge/skills/__init__.py` (export `wire_skills`)
- Test: `tests/test_skills_wiring.py`

Rationale: keep the REPL-integration logic in a unit-testable helper so the cli.py change is a one-line call.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skills_wiring.py
"""wire_skills registers use_skill + appends the index ONLY when skills exist."""

from __future__ import annotations

from modelbridge.agent.tools import build_default_registry
from modelbridge.skills.wiring import wire_skills


def _write_skill(proj, name, desc):
    d = proj / ".modelbridge" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


def test_wire_skills_no_skills_is_noop(tmp_path):
    reg = build_default_registry(include_bash=False)
    base_names = set(reg.names())
    out = wire_skills(reg, "SYS", project_path=tmp_path)
    assert out == "SYS"  # unchanged system prompt
    assert "use_skill" not in reg.names()  # tool NOT registered
    assert set(reg.names()) == base_names


def test_wire_skills_registers_tool_and_appends_index(tmp_path):
    _write_skill(tmp_path, "deploy", "部署到生产")
    reg = build_default_registry(include_bash=False)
    out = wire_skills(reg, "SYS", project_path=tmp_path)
    assert "use_skill" in reg.names()
    assert "SYS" in out and "deploy: 部署到生产" in out and "use_skill" in out
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_skills_wiring.py -v`
Expected: FAIL — `ModuleNotFoundError: modelbridge.skills.wiring`.

- [ ] **Step 3: Implement the helper + wire cli.py**

Create `modelbridge/skills/wiring.py`:

```python
"""Wire user skills into a REPL session: register use_skill + index the system prompt."""

from __future__ import annotations

from pathlib import Path

from ..agent.tools import ToolRegistry
from ..agent.tools.skill_tool import UseSkillTool
from .discovery import build_skills_index, discover_skills


def wire_skills(
    registry: ToolRegistry, system_prompt: str, *, project_path: Path | str | None = None
) -> str:
    """If any skills exist, register the use_skill tool and return the system
    prompt with the skill index appended; otherwise return it unchanged.

    Returns the (possibly augmented) system prompt text.
    """
    skills = discover_skills(project_path)
    if not skills:
        return system_prompt
    registry.register(UseSkillTool(project_path=project_path))
    index = build_skills_index(skills)
    return f"{system_prompt}\n\n{index}"
```

Add `wire_skills` to `modelbridge/skills/__init__.py`'s imports + `__all__`.

In `modelbridge/cli.py`, in the REPL command (where `registry` is built and `sys_prompt_text` is defined, around the lines that read `sys_prompt_text = system or _default_system_prompt(...)` and `prompt_builder = PromptBuilder().with_system_prompt(sys_prompt_text)...`): immediately AFTER `sys_prompt_text` is assigned and BEFORE it is used by `PromptBuilder`, insert:

```python
        from .skills.wiring import wire_skills
        _n_skills_before = len(registry.names())
        sys_prompt_text = wire_skills(registry, sys_prompt_text, project_path=cwd_resolved)
        if "use_skill" in registry.names():
            from .skills.discovery import discover_skills as _ds
            _n = len(_ds(cwd_resolved))
            if on_system_like_available:  # use the existing REPL system-notice mechanism
                ...
```

For the startup note: reuse however the REPL prints its ready/system line (search cli.py for where it prints the REPL banner / `tools=` / `on_system`). Add a line like `已加载 {n} 个用户 skill（mbridge skill list 查看）`. If a clean hook isn't obvious, print via the module `console` right after wiring. KEEP IT SIMPLE — the load-bearing part is the tool registration + index append; the banner note is cosmetic. (Confirm the exact variable name for the resolved project dir — it may be `cwd_resolved` or `ctx.cwd`; use whatever the surrounding REPL code already uses for the working directory.)

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_skills_wiring.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the REPL smoke check + full suite**

Run: `py -3.11 -m pytest -q`
Expected: all pass (no regression in existing REPL/cli tests).

- [ ] **Step 6: Commit**

```bash
git add modelbridge/skills/wiring.py modelbridge/skills/__init__.py modelbridge/cli.py tests/test_skills_wiring.py
git commit -m "feat(skills): wire use_skill + skill index into the REPL system prompt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `mbridge skill` CLI group (with security warning on add)

**Files:**
- Create: `modelbridge/skills/cli.py`
- Modify: `modelbridge/cli.py` (mount the group)
- Test: `tests/test_skill_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_cli.py
"""mbridge skill list/show/add/remove + the security warning on add."""

from __future__ import annotations

from typer.testing import CliRunner

from modelbridge.cli import app

runner = CliRunner()


def _seed_skill_src(tmp_path, name="deploy", desc="部署"):
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n步骤\n", encoding="utf-8")
    return src


def test_skill_list_shows_added(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path / "home"))
    # place a skill directly under the global dir
    g = tmp_path / "home" / "skills" / "deploy"
    g.mkdir(parents=True)
    (g / "SKILL.md").write_text("---\nname: deploy\ndescription: 部署到生产\n---\nx\n", encoding="utf-8")
    r = runner.invoke(app, ["skill", "list"])
    assert r.exit_code == 0
    assert "deploy" in r.output and "部署到生产" in r.output


def test_skill_add_warns_and_requires_confirmation_then_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path / "home"))
    src = _seed_skill_src(tmp_path)
    # answer "yes" to the typed confirmation
    r = runner.invoke(app, ["skill", "add", str(src)], input="yes\n")
    assert r.exit_code == 0
    assert "后果自负" in r.output or "自行核实" in r.output  # the safety warning showed
    assert (tmp_path / "home" / "skills" / "deploy" / "SKILL.md").exists()


def test_skill_add_aborts_when_not_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path / "home"))
    src = _seed_skill_src(tmp_path)
    r = runner.invoke(app, ["skill", "add", str(src)], input="n\n")
    assert not (tmp_path / "home" / "skills" / "deploy").exists()


def test_skill_remove_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path / "home"))
    g = tmp_path / "home" / "skills" / "deploy"
    g.mkdir(parents=True)
    (g / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nx\n", encoding="utf-8")
    r = runner.invoke(app, ["skill", "remove", "deploy"], input="y\n")
    assert r.exit_code == 0
    assert not g.exists()
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_skill_cli.py -v`
Expected: FAIL — `skill` is not a known command (exit_code 2) / module missing.

- [ ] **Step 3: Implement the CLI group**

Create `modelbridge/skills/cli.py`:

```python
"""`mbridge skill ...` — manage user skills. Adding warns loudly (skills are
arbitrary instructions the AI will execute)."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.prompt import Confirm
from rich.table import Table

from ..cli_console import console, err_console
from ..utils import get_app_dir
from .discovery import discover_skills, find_skill, parse_skill

skill_app = typer.Typer(
    name="skill",
    help="用户自定义 skill (list / show / add / remove)。",
    no_args_is_help=True,
)

_ADD_WARNING = (
    "⚠ skill 是会被 AI 读取并执行的任意指令，可能包含恶意内容"
    "（诱导 AI 删文件、泄露密钥、执行命令等）。\n"
    "  请自行核实来源与内容是否安全。一切后果由你自负，"
    "ModelBridge 维护者不承担责任。"
)


def _global_skills_dir() -> Path:
    return get_app_dir() / "skills"


@skill_app.command("list")
def cmd_skill_list() -> None:
    """列出已发现的 skill（全局 + 当前目录项目）。"""
    skills = discover_skills(project_path=Path.cwd())
    if not skills:
        console.print("[dim]没有 skill。用 `mbridge skill add <路径>` 添加，或把 SKILL.md 文件夹放进 ~/.modelbridge/skills/[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("scope")
    table.add_column("description")
    for s in skills:
        table.add_row(s.name, s.scope, s.description)
    console.print(table)


@skill_app.command("show")
def cmd_skill_show(name: str = typer.Argument(..., help="skill 名")) -> None:
    """打印某个 skill 的完整正文。"""
    s = find_skill(name, project_path=Path.cwd())
    if s is None:
        err_console.print(f"[red]未找到 skill '{name}'。[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]# {s.name}[/bold]  [dim]({s.scope}) {s.path}[/dim]\n")
    console.print(s.body)


@skill_app.command("add")
def cmd_skill_add(
    path: Path = typer.Argument(..., help="本地 skill 文件夹路径（内含 SKILL.md）。"),
) -> None:
    """把一个本地 skill 文件夹拷进 ~/.modelbridge/skills/（仅本地路径）。"""
    src = path.expanduser().resolve()
    md = src / "SKILL.md"
    if not md.is_file():
        err_console.print(f"[red]{src} 下没有 SKILL.md。[/red]")
        raise typer.Exit(code=1)
    sk = parse_skill(md, scope="global")
    if sk is None:
        err_console.print("[red]SKILL.md 解析失败（需要 frontmatter 的 name + description）。[/red]")
        raise typer.Exit(code=1)
    err_console.print(f"[bold yellow]{_ADD_WARNING}[/bold yellow]")
    if not Confirm.ask(f"确认添加 skill «{sk.name}»？", default=False):
        console.print("[yellow]已取消。[/yellow]")
        raise typer.Exit(code=0)
    dst = _global_skills_dir() / sk.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    console.print(f"[green]✓ 已添加 skill «{sk.name}» → {dst}[/green]")


@skill_app.command("remove")
def cmd_skill_remove(name: str = typer.Argument(..., help="skill 名")) -> None:
    """删除一个全局 skill。"""
    dst = _global_skills_dir() / name
    if not dst.is_dir():
        err_console.print(f"[red]全局 skill '{name}' 不存在。[/red]")
        raise typer.Exit(code=1)
    if not Confirm.ask(f"删除 skill «{name}»（{dst}）？", default=False):
        console.print("[yellow]已取消。[/yellow]")
        raise typer.Exit(code=0)
    shutil.rmtree(dst)
    console.print(f"[green]✓ 已删除 skill «{name}»。[/green]")


__all__ = ["skill_app"]
```

In `modelbridge/cli.py`, mount the group next to the other `app.add_typer(...)` calls:

```python
from .skills.cli import skill_app  # noqa: E402

app.add_typer(skill_app, name="skill")
```

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_skill_cli.py -v`
Expected: PASS (4 tests). If `Confirm.ask` doesn't read the CliRunner `input=`, adapt the test to the actual prompt mechanism (e.g. use `typer.confirm`) — but keep the typed-confirmation requirement.

- [ ] **Step 5: Commit**

```bash
git add modelbridge/skills/cli.py modelbridge/cli.py tests/test_skill_cli.py
git commit -m "feat(skills): mbridge skill list/show/add/remove with a loud add-warning

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: README + final verification

**Files:**
- Modify: `README.md`
- Test: full suite + ruff

- [ ] **Step 1: Add a README section**

Add a `## Skills (用户自定义)` section (place it after the browser side-panel section, before `## 项目结构`). Cover: what a skill is (`<name>/SKILL.md`, Claude-Code compatible), where they live (`~/.modelbridge/skills/` + project `.modelbridge/skills/`), the commands (`mbridge skill list/show/add/remove`), how the AI uses them (index in the prompt → `use_skill` → confirm), AND the **security disclaimer**:

> ⚠️ skill 是会被 AI 读取并执行的任意指令。添加来路不明的 skill 等于让 AI 执行陌生人的指令——可能删文件、泄露密钥、跑命令。请只添加你信任并亲自审阅过的 skill；加载时务必看清来源再确认。**后果自负，ModelBridge 维护者不承担责任。**

Also add `mbridge skill list / show / add / remove   用户自定义 skill` to the `## 命令一览` block (near the other management groups).

- [ ] **Step 2: Full suite + lint**

Run: `py -3.11 -m pytest -q`
Expected: all pass.

Run: `py -3.11 -m ruff check .`
Expected: All checks passed.

Run (sanity — the new command resolves and hides nothing): `py -3.11 -c "from typer.testing import CliRunner; from modelbridge.cli import app; print(CliRunner().invoke(app, ['skill','--help']).exit_code)"`
Expected: `0`

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document Skills feature + security disclaimer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** §2 format → Task 1 (parse_skill). §3 discovery → Task 1. §4 index → Tasks 1/2 + wiring Task 4. §5 use_skill tool (find→confirm→body, allow_always, truncation) → Task 3. §6 CLI list/show/add/remove → Task 5. §7 security (add warning + disclaimer + load confirm + README) → Tasks 3 (load confirm), 5 (add warning), 6 (README disclaimer). REPL startup note → Task 4 (cosmetic, flagged). §8 error handling (malformed skip, unknown name, missing dir) → Tasks 1/3. §9 testing → each task's tests. All covered.
- **Placeholder scan:** Task 4's startup-note step is intentionally loose (cosmetic, depends on the exact REPL banner hook) and is flagged as such; the load-bearing wiring (tool register + index append) is fully specified + tested. No other placeholders.
- **Type/name consistency:** `Skill(name, description, body, path, scope)`, `parse_skill(md, *, scope)`, `discover_skills(project_path)`, `find_skill(name, project_path)`, `build_skills_index(skills)`, `wire_skills(registry, system_prompt, *, project_path)`, `UseSkillTool(project_path)` — consistent across tasks. `Tool.err/ok`, `ToolResult`, `ctx.confirm(...)` match `modelbridge/agent/tools/base.py` + `context.py` read during planning. `get_app_dir` / `get_logger` from `modelbridge.utils` (verify `get_logger` name in Task 1).
