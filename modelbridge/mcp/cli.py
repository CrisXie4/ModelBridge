"""``mbridge mcp ...`` subcommands: inspect and exercise configured servers.

Kept in the mcp package (with its own ``Console``) so the top-level
``cli.py`` only needs ``add_typer`` — no import cycle.

Commands:

* ``mbridge mcp list``      — server connection status table
* ``mbridge mcp tools``     — all discovered tools (qualified names)
* ``mbridge mcp resources`` — all discovered resources
* ``mbridge mcp prompts``   — all discovered prompts
* ``mbridge mcp call``      — invoke one tool with JSON args (manual test)
* ``mbridge mcp read``      — read a resource by uri
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from .config import load_mcp_settings
from .errors import MCPError
from .manager.manager import MCPManager

console = Console()
err_console = Console(stderr=True)

mcp_app = typer.Typer(
    name="mcp",
    help="MCP 客户端：连接并调用外部 MCP server (list / tools / resources / prompts / call / read)。",
    invoke_without_command=True,
)


@mcp_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _list_impl()


def _open_manager(*, verbose: bool = False) -> MCPManager:
    settings = load_mcp_settings()
    if not settings.servers:
        err_console.print(
            "[yellow]未配置任何 MCP server。[/yellow]\n"
            "在 ~/.modelbridge/config.yaml 顶层加入:\n\n"
            "mcp:\n  enabled: true\n  servers:\n"
            "    - id: filesystem\n      transport: stdio\n"
            "      command: npx\n      args: ['-y', '@modelcontextprotocol/server-filesystem', '.']\n"
        )
        raise typer.Exit(code=2)
    manager = MCPManager(settings=settings, verbose=verbose)
    manager.connect_all()
    return manager


@mcp_app.command("list")
def list_servers(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """显示每个 MCP server 的连接状态与能力计数。"""
    _list_impl(verbose=verbose)


def _list_impl(*, verbose: bool = False) -> None:
    manager = _open_manager(verbose=verbose)
    try:
        table = Table(title="MCP servers")
        table.add_column("id")
        table.add_column("state")
        table.add_column("server")
        table.add_column("tools", justify="right")
        table.add_column("resources", justify="right")
        table.add_column("prompts", justify="right")
        table.add_column("note")
        for s in manager.statuses():
            state_color = {
                "ready": "green", "failed": "red", "disabled": "dim",
            }.get(s.state, "yellow")
            note = s.error or ""
            sv = f"{s.server_name} {s.server_version}".strip()
            table.add_row(
                s.server_id, f"[{state_color}]{s.state}[/{state_color}]", sv,
                str(s.tools), str(s.resources), str(s.prompts), note,
            )
        console.print(table)
    finally:
        manager.shutdown()


@mcp_app.command("tools")
def list_tools() -> None:
    """列出所有已发现的工具（限定名 <server>__<tool>）。"""
    manager = _open_manager()
    try:
        table = Table(title="MCP tools")
        table.add_column("qualified name")
        table.add_column("server")
        table.add_column("description")
        for qt in manager.catalog.tools:
            table.add_row(qt.qualified_name, qt.server_id, (qt.tool.description or "")[:80])
        console.print(table)
    finally:
        manager.shutdown()


@mcp_app.command("resources")
def list_resources() -> None:
    """列出所有已发现的资源。"""
    manager = _open_manager()
    try:
        table = Table(title="MCP resources")
        table.add_column("uri")
        table.add_column("server")
        table.add_column("name")
        table.add_column("mime")
        for qr in manager.catalog.resources:
            r = qr.resource
            table.add_row(r.uri, qr.server_id, r.name, r.mime_type or "")
        console.print(table)
    finally:
        manager.shutdown()


@mcp_app.command("prompts")
def list_prompts() -> None:
    """列出所有已发现的 prompt 模板。"""
    manager = _open_manager()
    try:
        table = Table(title="MCP prompts")
        table.add_column("qualified name")
        table.add_column("server")
        table.add_column("args")
        for qp in manager.catalog.prompts:
            args = ", ".join(a.name for a in qp.prompt.arguments)
            table.add_row(qp.qualified_name, qp.server_id, args)
        console.print(table)
    finally:
        manager.shutdown()


@mcp_app.command("call")
def call_tool(
    name: str = typer.Argument(..., help="限定名 <server>__<tool>"),
    args_json: str = typer.Option("{}", "--args", "-a", help="JSON 形式的参数对象"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """手动调用一个 MCP 工具（用于联调）。"""
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as e:
        err_console.print(f"[red]--args 不是合法 JSON: {e}[/red]")
        raise typer.Exit(code=2) from e
    if not isinstance(args, dict):
        err_console.print("[red]--args 必须是 JSON 对象[/red]")
        raise typer.Exit(code=2)

    manager = _open_manager(verbose=verbose)
    try:
        result = manager.call_tool(name, args)
        if result.is_error:
            err_console.print("[red]工具返回错误:[/red]")
        console.print(result.joined_text())
    except MCPError as e:
        err_console.print(f"[red]{e.display()}[/red]")
        raise typer.Exit(code=1) from e
    finally:
        manager.shutdown()


@mcp_app.command("read")
def read_resource(
    uri: str = typer.Argument(..., help="resource uri"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """读取一个 MCP 资源。"""
    manager = _open_manager(verbose=verbose)
    try:
        result = manager.read_resource(uri)
        console.print(result.joined_text())
    except MCPError as e:
        err_console.print(f"[red]{e.display()}[/red]")
        raise typer.Exit(code=1) from e
    finally:
        manager.shutdown()


__all__ = ["mcp_app"]
