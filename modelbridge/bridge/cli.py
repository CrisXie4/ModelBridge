"""``mbridge bridge ...`` subcommands: manage the Native Messaging host.

* ``mbridge bridge install --extension-id <id>`` — register the host so
  Chrome/Edge can launch it for the side-panel extension.
* ``mbridge bridge uninstall`` — remove the registration.
* ``mbridge bridge run`` — run the host on stdio (this is what the browser
  launches; exposed for manual smoke-testing via piped frames).
* ``mbridge bridge status`` — show where things are installed.

Kept in the bridge package (own ``Console``) so top-level ``cli.py`` only does
``add_typer``.
"""

from __future__ import annotations

import typer
from rich.console import Console

from . import HOST_NAME, install as installer

console = Console()
err_console = Console(stderr=True)

bridge_app = typer.Typer(
    name="bridge",
    help="浏览器侧边栏的 Native Messaging 宿主 (install / uninstall / run / status)。",
    invoke_without_command=True,
)


@bridge_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _status_impl()


@bridge_app.command("install")
def cmd_install(
    extension_id: str = typer.Option(
        None,
        "--extension-id",
        "-e",
        help="扩展 ID (在 chrome://extensions 加载 unpacked 后复制)。省略则用上次保存的。",
    ),
    chrome_only: bool = typer.Option(False, "--chrome-only", help="只注册 Chrome (不含 Edge)。"),
) -> None:
    """注册 Native Messaging 宿主到 Chrome/Edge。"""
    ext_id = extension_id or installer.load_saved_extension_id()
    if not ext_id:
        err_console.print(
            "[red]需要扩展 ID。[/red]\n"
            "先在 [bold]chrome://extensions[/bold] 打开开发者模式、"
            "[bold]加载已解压的扩展程序[/bold] 选择 extension/ 目录，"
            "复制生成的 ID，然后:\n\n"
            "    mbridge bridge install --extension-id <粘贴ID>\n"
        )
        raise typer.Exit(code=2)

    browsers = ("chrome",) if chrome_only else installer.DEFAULT_BROWSERS
    try:
        result = installer.install(ext_id, browsers=browsers)
    except ValueError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    console.print("[green]✓ LocalBridge 宿主已注册[/green]")
    console.print(f"  manifest : {result.manifest_path}")
    console.print(f"  launcher : {result.launcher_path}")
    console.print(f"  扩展 ID  : {result.extension_id}")
    for line in result.registered:
        console.print(f"  {line}")
    console.print(
        "\n[yellow]提示[/yellow]: API key 必须在 keyring/config.yaml 里 "
        "(浏览器从 GUI 启动宿主，读不到 shell 的环境变量)。\n"
        "在 chrome://extensions 里 [bold]重新加载[/bold] 扩展后即可在侧边栏使用。"
    )


@bridge_app.command("uninstall")
def cmd_uninstall(
    chrome_only: bool = typer.Option(False, "--chrome-only", help="只移除 Chrome。"),
) -> None:
    """移除 Native Messaging 宿主注册。"""
    browsers = ("chrome",) if chrome_only else installer.DEFAULT_BROWSERS
    for line in installer.uninstall(browsers=browsers):
        console.print(f"  {line}")


control_app = typer.Typer(
    name="control",
    help="命令行联动开关 (默认关闭)：开启后 `mbridge browser` 才能驱动浏览器。",
    invoke_without_command=True,
)
bridge_app.add_typer(control_app, name="control")


@control_app.callback(invoke_without_command=True)
def _control_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _control_status_impl()


@control_app.command("on")
def cmd_control_on(
    token: str = typer.Option(
        None, "--token", "-t", help="自定义 token；省略则自动生成一串随机值。"
    ),
) -> None:
    """开启命令行联动 (生成/设置 token)。开启后需重新打开侧边栏让宿主生效。"""
    data = installer_control().set_control(enabled=True, token=token)
    console.print("[green]✓ 命令行联动已开启[/green]")
    console.print(f"  token : {data['token']}")
    console.print(
        "\n[yellow]下一步[/yellow]：到 chrome://extensions [bold]重新打开/刷新[/bold] 侧边栏"
        "让宿主带着这个开关重启，然后即可：\n    mbridge browser \"在 GitHub 搜索 modelbridge\"\n"
        "\n关闭：mbridge bridge control off"
    )


@control_app.command("off")
def cmd_control_off() -> None:
    """关闭命令行联动 (宿主下次启动不再监听本地端口)。"""
    installer_control().set_control(enabled=False)
    console.print("[green]✓ 命令行联动已关闭[/green] (重开侧边栏后宿主不再监听)。")


@control_app.command("status")
def cmd_control_status() -> None:
    """查看命令行联动开关状态。"""
    _control_status_impl()


def _control_status_impl() -> None:
    ctrl = installer_control()
    cfg = ctrl.load_control_config()
    state = "[green]开启[/green]" if cfg.get("enabled") else "[red]关闭[/red]"
    console.print(f"[bold]命令行联动[/bold]: {state}")
    if cfg.get("token"):
        console.print(f"  token    : {cfg['token']}")
    running = ctrl.endpoint_path().exists()
    console.print(f"  宿主监听 : {'是' if running else '否'} ({ctrl.endpoint_path()})")
    if not cfg.get("enabled"):
        console.print("  开启: [bold]mbridge bridge control on[/bold]，然后重开侧边栏。")


def installer_control():
    """Lazy import to keep `bridge install/status` from loading the socket code."""
    from . import control

    return control


@bridge_app.command("run")
def cmd_run() -> None:
    """在 stdio 上运行宿主 (浏览器会自动调用；手动用于冒烟测试)。"""
    # Import lazily so the heavy engine isn't loaded for install/status.
    from .host import main

    main()


@bridge_app.command("status")
def cmd_status() -> None:
    """显示宿主安装位置与已保存的扩展 ID。"""
    _status_impl()


def _status_impl() -> None:
    console.print(f"[bold]LocalBridge[/bold]  host={HOST_NAME}")
    console.print(f"  manifest : {installer.manifest_path()}  "
                  f"({'存在' if installer.manifest_path().exists() else '未安装'})")
    console.print(f"  launcher : {installer.launcher_path()}  "
                  f"({'存在' if installer.launcher_path().exists() else '未安装'})")
    saved = installer.load_saved_extension_id()
    console.print(f"  扩展 ID  : {saved or '(未保存，运行 install --extension-id)'}")


__all__ = ["bridge_app"]
