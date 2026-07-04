"""``mbridge skill ...`` subcommands: manage user-installed skills.

Commands:
  * ``mbridge skill list``          — table of all discovered global skills
  * ``mbridge skill show <name>``   — print full body of a skill
  * ``mbridge skill add <path>``    — copy a local skill folder into ~/.modelbridge/skills/
  * ``mbridge skill remove <name>`` — delete a skill from ~/.modelbridge/skills/

Kept in the skills package so top-level ``cli.py`` only needs ``add_typer``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from ..cli_console import console, err_console
from ..utils import get_app_dir
from .discovery import discover_skills, find_skill


skill_app = typer.Typer(
    name="skill",
    help="用户 Skill 管理 (list / show / add / remove)。",
    no_args_is_help=True,
)


def _global_skills_dir() -> Path:
    """Return (and create if missing) the global skills directory."""
    d = get_app_dir() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@skill_app.command("list")
def cmd_list() -> None:
    """列出所有已安装的全局 Skills。"""
    skills = discover_skills()
    if not skills:
        console.print("[dim]未安装任何 skill。使用 `mbridge skill add <路径>` 安装。[/dim]")
        return

    table = Table(title="已安装 Skills", show_header=True, header_style="bold cyan")
    table.add_column("名称", style="bold")
    table.add_column("作用域")
    table.add_column("描述")

    for sk in skills:
        table.add_row(sk.name, sk.scope, sk.description)

    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@skill_app.command("show")
def cmd_show(
    name: str = typer.Argument(..., help="Skill 名称。"),
) -> None:
    """显示指定 Skill 的完整内容。"""
    sk = find_skill(name)
    if sk is None:
        err_console.print(f"[red]找不到 skill '{name}'。运行 `mbridge skill list` 查看可用列表。[/red]")
        raise typer.Exit(code=1)

    console.print(
        Panel(
            sk.body,
            title=f"[bold]{sk.name}[/bold]  [{sk.scope}]  {sk.description}",
            border_style="cyan",
        )
    )


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@skill_app.command("add")
def cmd_add(
    path: Path = typer.Argument(..., help="包含 SKILL.md 的本地 skill 目录路径。"),
) -> None:
    """将本地 skill 文件夹安装到全局 skills 目录。

    ⚠ 安全警告：只安装你信任的来源的 skill。Skill 文件在 Agent 会话中会被注入为系统指令，
    恶意 skill 可能引导 Agent 执行危险操作。
    """
    path = path.resolve()

    # Validate the source directory and SKILL.md
    if not path.is_dir():
        err_console.print(f"[red]路径不存在或不是目录：{path}[/red]")
        raise typer.Exit(code=1)

    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        err_console.print(
            f"[red]目录中未找到 SKILL.md：{path}[/red]\n"
            "Skill 目录必须包含一个带有 YAML frontmatter (name + description) 的 SKILL.md 文件。"
        )
        raise typer.Exit(code=1)

    # LOUD security warning (must appear before the confirmation prompt)
    console.print(
        Panel(
            "[bold red]安全警告 / SECURITY WARNING[/bold red]\n\n"
            "你即将安装一个第三方 Skill。\n"
            "Skill 文件会在 Agent 会话中作为系统指令注入，"
            "[bold]恶意 skill 可能引导 Agent 读取、修改或泄漏你的文件。[/bold]\n\n"
            f"来源路径：[bold]{path}[/bold]\n\n"
            "[yellow]只安装你完全信任的来源的 skill。[/yellow]\n"
            "继续前请先阅读 SKILL.md 内容确认安全。",
            title="[bold red]⚠ 危险操作 ⚠[/bold red]",
            border_style="red",
        )
    )

    # Confirmation — typer.confirm reads stdin reliably under CliRunner
    confirmed = typer.confirm("确认安装此 skill?", default=False)
    if not confirmed:
        console.print("[yellow]已中止。Skill 未安装。[/yellow]")
        raise typer.Exit(code=0)

    # Copy the skill folder into the global skills directory
    dest = _global_skills_dir() / path.name
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(str(path), str(dest))
    except OSError as e:
        err_console.print(f"[red]安装失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    console.print(f"[green]✓ Skill '[bold]{path.name}[/bold]' 已安装到 {dest}[/green]")


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


