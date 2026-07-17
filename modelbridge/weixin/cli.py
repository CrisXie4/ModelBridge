"""``mbridge weixin ...`` + 顶层 ``mbridge gateway`` 子命令。

* ``mbridge weixin login``     — 扫码登录 iLink Bot，拿到 bot_token 写入 ~/.modelbridge/weixin.json
* ``mbridge weixin status``    — 查看登录状态
* ``mbridge weixin logout``    — 清除凭据
* ``mbridge weixin test``      — 快速 ping 一次 iLink，确认凭据有效

* ``mbridge gateway``          — 启动微信网关（默认），或 ``mbridge gateway --channel bridge`` 跑浏览器侧边栏
* ``mbridge gateway gui``      — 浏览器侧边栏模式别名
"""

from __future__ import annotations

import time
from typing import Optional

import typer
from rich.panel import Panel

# 共用 cli_console 的两个单例：导入即把 stdin/stdout/stderr 重设为 UTF-8，
# Windows gbk 终端才能渲染二维码的 ▀ 半块字符与 ✓/✗ 符号。
from ..cli_console import console, err_console
from .creds import (
    clear_credentials,
    load_credentials,
    save_credentials,
)


def _render_qrcode(data: str) -> bool:
    """把 ``data`` 渲染成终端可扫的二维码（黑底块=深色模块，白底块=浅色）。

    用 ▀（上半块）一行渲染两行模块，前景色=上像素、背景色=下像素，并强制
    黑/白配色，这样无论终端是亮色还是暗色主题，扫出来都是标准「深色在浅底」的
    二维码。渲染成功返回 True；缺 ``qrcode`` 库或出错返回 False（调用方回退到
    打开浏览器/复制链接）。
    """
    if not data:
        return False
    try:
        import qrcode  # 可选依赖；没装则回退
        from rich.text import Text
    except Exception:
        return False
    try:
        qr = qrcode.QRCode(
            border=2,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
        )
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()  # list[list[bool]]，True=深色模块
        if not matrix:
            return False
        width = len(matrix[0])
        rows = list(matrix)
        if len(rows) % 2:  # 补一行浅色，凑成偶数行好两两配对
            rows.append([False] * width)
        dark, light = "#000000", "#ffffff"
        text = Text()
        for y in range(0, len(rows), 2):
            top, bottom = rows[y], rows[y + 1]
            for x in range(width):
                fg = dark if top[x] else light
                bg = dark if bottom[x] else light
                text.append("▀", style=f"{fg} on {bg}")
            text.append("\n")
        console.print(text, end="")
        return True
    except Exception:
        return False

weixin_app = typer.Typer(
    name="weixin",
    help="微信 iLink Bot 通道：login / status / logout / test。",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# weixin login — 扫码登录
# ---------------------------------------------------------------------------

@weixin_app.command("login")
def cmd_login(
    baseurl: Optional[str] = typer.Option(
        None, "--baseurl", "-b",
        help="iLink API 基础地址（默认 https://ilinkai.weixin.qq.com）。",
    ),
    poll_timeout: float = typer.Option(
        60.0, "--poll-timeout",
        help="单次扫码状态请求的网络超时(秒)；两次轮询之间会停 2 秒。",
    ),
    max_wait: float = typer.Option(
        300.0, "--max-wait",
        help="等待扫码的最长时间(秒)；超时即放弃。",
    ),
) -> None:
    """扫码登录微信 iLink Bot，拿到 bot_token。"""
    from .client import WeixinClient, WeixinError

    console.print("[bold cyan]微信 iLink Bot 登录[/bold cyan]")
    try:
        qr = WeixinClient.fetch_qrcode(baseurl=baseurl)
    except WeixinError as e:
        err_console.print(f"[red]获取二维码失败：{e}[/red]")
        raise typer.Exit(code=1) from e

    qrcode_url = qr.get("qrcode_url", "")
    qrcode_token = qr.get("qrcode", "")
    if not qrcode_token:
        err_console.print("[red]服务端没返回二维码 token[/red]")
        raise typer.Exit(code=1)

    # 优先在终端里直接画出可扫二维码：编码服务端给的扫码内容（拿不到就退回 token）。
    scan_payload = qrcode_url or qrcode_token
    console.print("[bold cyan]用微信「扫一扫」扫描下面的二维码登录：[/bold cyan]\n")
    rendered = _render_qrcode(scan_payload)
    if rendered:
        console.print()
        if qrcode_url:
            console.print(f"[grey]扫不出来？把这个链接复制到手机浏览器打开：[/grey]\n[blue]{qrcode_url}[/blue]")
    else:
        # 没有 qrcode 库或渲染失败 → 退回原来的链接 + 打开浏览器方案。
        console.print(Panel(
            f"终端画不出二维码，请用微信扫描这个链接对应的二维码：\n\n[blue]{qrcode_url}[/blue]\n\n"
            f"或把链接复制到浏览器打开后扫码。",
            title="微信扫码登录", border_style="cyan",
        ))
        opened = False
        try:
            import webbrowser
            opened = webbrowser.open(qrcode_url)
        except Exception:
            opened = False
        if not opened:
            console.print("[yellow]无法自动打开浏览器，请手动复制上方链接。[/yellow]")

    # 轮询扫码状态
    deadline = time.time() + max_wait
    last_status = None
    while time.time() < deadline:
        try:
            st = WeixinClient.poll_qrcode_status(
                qrcode_token, baseurl=baseurl, timeout=poll_timeout
            )
        except WeixinError as e:
            console.print(f"[yellow]轮询出错(将重试)：{e}[/yellow]")
            time.sleep(2.0)
            continue

        status = st.get("status", "wait")
        if status != last_status:
            label = {
                "wait": "[grey]等待扫码…[/grey]",
                "scaned": "[yellow]已扫描，请在手机上确认[/yellow]",
                "confirmed": "[green]已确认[/green]",
                "expired": "[red]二维码已过期[/red]",
            }.get(status, status)
            console.print(f"  {label}")
            last_status = status

        if status == "expired":
            err_console.print("[red]二维码过期，请重新执行 `mbridge weixin login`。[/red]")
            raise typer.Exit(code=2)
        if status == "confirmed":
            creds = st.get("credentials") or {}
            if not creds.get("bot_token"):
                err_console.print("[red]确认成功但没拿到 bot_token[/red]")
                raise typer.Exit(code=1)
            # 落地 baseurl（确认时返回的可能与默认不同）
            out = dict(creds)
            if st.get("baseurl"):
                out["baseurl"] = st["baseurl"]
            path = save_credentials(out)
            console.print(Panel(
                f"[green]✓ 登录成功[/green]\n"
                f"  bot_token : {creds.get('bot_token', '')[:16]}…\n"
                f"  bot_id    : {creds.get('ilink_bot_id', '')}\n"
                f"  user_id   : {creds.get('ilink_user_id', '')}\n"
                f"  baseurl   : {out.get('baseurl', '(默认)')}\n"
                f"  保存到    : {path}",
                title="微信 iLink Bot", border_style="green",
            ))
            console.print(
                "\n下一步：[bold]mbridge gateway[/bold] 启动微信网关，"
                "在微信里 @ 你的 iLink Bot 就能对话。"
            )
            return
        # wait / scaned：get_qrcode_status 是普通轮询（只有 getupdates 才长轮询），
        # 服务端会立即返回，所以这里必须歇一下再问，否则空转打爆服务端 + 占满 CPU。
        time.sleep(2.0)
    err_console.print("[red]等待扫码超时。[/red]")
    raise typer.Exit(code=3)


# ---------------------------------------------------------------------------
# weixin status / logout / test
# ---------------------------------------------------------------------------

@weixin_app.command("status")
def cmd_status() -> None:
    """查看微信登录状态。"""
    creds = load_credentials()
    if not creds:
        console.print("[grey]未登录。运行 `mbridge weixin login` 扫码。[/grey]")
        return
    console.print(Panel(
        f"  bot_token : {creds.get('bot_token', '')[:16]}…\n"
        f"  bot_id    : {creds.get('ilink_bot_id', '')}\n"
        f"  user_id   : {creds.get('ilink_user_id', '')}\n"
        f"  baseurl   : {creds.get('baseurl', '(默认)')}\n"
        f"  logged_at : {creds.get('logged_at', '?')}",
        title="微信 iLink Bot 状态", border_style="green",
    ))


@weixin_app.command("logout")
def cmd_logout() -> None:
    """清除微信凭据。"""
    clear_credentials()
    console.print("[green]✓ 微信凭据已清除[/green]")


@weixin_app.command("test")
def cmd_test() -> None:
    """快速 ping 一次 iLink，确认凭据可用。"""
    from .client import WeixinClient, WeixinError
    creds = load_credentials()
    if not creds:
        err_console.print("[red]未登录。先 `mbridge weixin login`。[/red]")
        raise typer.Exit(code=2)
    cli = WeixinClient(
        bot_token=creds.get("bot_token", ""),
        bot_id=creds.get("ilink_bot_id"),
        user_id=creds.get("ilink_user_id"),
        baseurl=creds.get("baseurl"),
    )
    try:
        cfg = cli.get_config()
    except WeixinError as e:
        err_console.print(f"[red]连接失败：{e}[/red]")
        raise typer.Exit(code=1) from e
    console.print(f"[green]✓ 连接正常[/green]\n  {cfg}")


__all__ = ["weixin_app"]
