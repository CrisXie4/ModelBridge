"""``mbridge search ...`` 子命令。

* ``mbridge search login [--server URL]`` — OAuth 授权,拿到 apikey + crypto_key 写入 ~/.modelbridge/search.json
* ``mbridge search status``  — 查看登录状态 / 套餐 / 配额
* ``mbridge search logout``  — 清除凭据
* ``mbridge search test [query]`` — 加密调用一次 /v1/search 验证连通

登录流程(无需本地 HTTP server,服务端已提供回调页 + 轮询端点):

1. 生成 PKCE + state,打开浏览器到 ``/oauth/authorize?...``
   (redirect_uri 指向服务端 ``/oauth/callback/local`` 回调页)
2. 用户在浏览器登录并同意,服务端把 code 暂存
3. 本地轮询 ``/oauth/wait/{state}`` 拿到 code
4. ``POST /oauth/token`` 用 code + verifier 换 token(响应含 api_key + crypto_key)
"""

from __future__ import annotations

import time
from typing import Optional

import typer
from rich.panel import Panel

# 共用 cli_console 的两个单例:导入即把 stdin/stdout/stderr 重设为 UTF-8。
from ..cli_console import console, err_console
from . import OAUTH_MAX_WAIT, default_server
from .client import (
    SearchError,
    build_authorize_url,
    do_search,
    exchange_token,
    pkce_pair,
    poll_state,
)
from .creds import (
    apikey_fingerprint,
    clear_credentials,
    load_credentials,
    save_credentials,
)

search_app = typer.Typer(
    name="search",
    help="ModelBridge 联网搜索:login / status / logout / test。",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# search login — OAuth 授权码 + PKCE
# ---------------------------------------------------------------------------

@search_app.command("login")
def cmd_login(
    server: Optional[str] = typer.Option(
        None, "--server", "-s",
        help="ModelBridge 服务端地址(默认 https://web.crisxie.top,可用环境变量 MODELBRIDGE_SERVER 覆盖)。",
    ),
    max_wait: float = typer.Option(
        OAUTH_MAX_WAIT, "--max-wait",
        help="等待在浏览器完成授权的最长时间(秒)。",
    ),
    poll_timeout: float = typer.Option(
        30.0, "--poll-timeout",
        help="单次轮询请求的网络超时(秒);两次轮询之间停 2 秒。",
    ),
) -> None:
    """登录 ModelBridge 服务端,获取联网搜索凭据。"""
    # 付费联网搜索当前为开发中状态(总开关 SEARCH_ENABLED=False)。
    # 保留子命令入口让用户能看到这个功能存在,但 login 直接拦截,避免
    # 走完 OAuth 流程拿到一个用不了的凭据。功能完工后把开关打开即可。
    from .wiring import SEARCH_ENABLED

    if not SEARCH_ENABLED:
        console.print(
            "[yellow]联网搜索功能正在开发中,暂未开放。[/yellow]\n"
            "[grey]代码已上传,功能完成后会在新版本启用。[/grey]"
        )
        raise typer.Exit(code=0)
    import secrets as _secrets

    endpoint = (server or default_server()).rstrip("/")
    redirect_uri = endpoint + "/oauth/callback/local"

    console.print("[bold cyan]ModelBridge 联网搜索 · 登录[/bold cyan]")
    console.print(f"  服务端: [blue]{endpoint}[/blue]\n")

    verifier, challenge = pkce_pair()
    state = _secrets.token_urlsafe(16)
    url = build_authorize_url(
        endpoint, state=state, challenge=challenge, redirect_uri=redirect_uri
    )

    console.print("[bold cyan]正在打开浏览器登录…[/bold cyan]")
    console.print(f"[grey]若浏览器未自动打开,请手动访问:[/grey]\n[blue]{url}[/blue]\n")
    opened = False
    try:
        import webbrowser

        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        console.print("[yellow]无法自动打开浏览器,请手动复制上方链接到浏览器。[/yellow]")
    console.print("[grey]完成登录并点击同意后,会自动回到这里继续。[/grey]\n")

    # 轮询授权状态
    deadline = time.time() + max_wait
    last_status = None
    code: str | None = None
    while time.time() < deadline:
        try:
            st = poll_state(endpoint, state, timeout=poll_timeout)
        except SearchError as exc:
            console.print(f"[yellow]轮询出错(将重试):{exc}[/yellow]")
            time.sleep(2.0)
            continue

        status = st.get("status", "pending")
        if status != last_status:
            label = {
                "pending": "[grey]等待在浏览器完成登录与授权…[/grey]",
                "ok": "[green]已获得授权码[/green]",
                "error": "[red]授权失败[/red]",
            }.get(status, status)
            console.print(f"  {label}")
            last_status = status

        if status == "error":
            err_console.print(
                f"[red]授权失败:{st.get('error_description') or st.get('error')}[/red]"
            )
            raise typer.Exit(code=2)
        if status == "ok":
            code = st.get("code")
            if not code:
                err_console.print("[red]授权成功但没拿到 code[/red]")
                raise typer.Exit(code=1)
            break
        time.sleep(2.0)

    if not code:
        err_console.print("[red]等待授权超时。[/red]")
        raise typer.Exit(code=3)

    # 换 token
    try:
        tok = exchange_token(
            endpoint, code=code, verifier=verifier, redirect_uri=redirect_uri
        )
    except SearchError as exc:
        err_console.print(f"[red]换取凭据失败:{exc}[/red]")
        raise typer.Exit(code=1) from exc

    expires_in = tok.get("expires_in")
    creds = {
        "endpoint": endpoint,
        "access_token": tok.get("access_token"),
        "refresh_token": tok.get("refresh_token"),
        "expires_at": int(time.time()) + int(expires_in) if expires_in else None,
        "api_key": tok.get("api_key"),
        "crypto_key": tok.get("crypto_key"),
        "plan_code": tok.get("plan_code"),
        "plan_name": tok.get("plan_name"),
        "monthly_quota": tok.get("monthly_quota"),
        "used_quota": tok.get("used_quota"),
        "remaining": tok.get("remaining"),
        "scope": tok.get("scope"),
    }
    path = save_credentials(creds)
    console.print(Panel(
        f"[green]✓ 登录成功[/green]\n"
        f"  api_key   : {apikey_fingerprint(creds['api_key'])}\n"
        f"  套餐      : {creds.get('plan_name') or creds.get('plan_code') or '?'}\n"
        f"  配额      : {creds.get('remaining', '?')} / {creds.get('monthly_quota', '?')} (剩余 / 月)\n"
        f"  保存到    : {path}",
        title="ModelBridge 联网搜索", border_style="green",
    ))
    console.print(
        "\n下一步:在 [bold]mbridge[/bold] 会话里直接问需要联网的问题,"
        "AI 会自动调用 web_search;或用 [bold]mbridge search test[/bold] 测试。"
    )


# ---------------------------------------------------------------------------
# search status / logout / test
# ---------------------------------------------------------------------------

@search_app.command("status")
def cmd_status() -> None:
    """查看联网搜索登录状态。"""
    creds = load_credentials()
    if not creds:
        console.print("[grey]未登录。运行 `mbridge search login`。[/grey]")
        return
    console.print(Panel(
        f"  服务端    : {creds.get('endpoint', '?')}\n"
        f"  api_key   : {apikey_fingerprint(creds.get('api_key'))}\n"
        f"  套餐      : {creds.get('plan_name') or creds.get('plan_code') or '?'}\n"
        f"  配额      : {creds.get('remaining', '?')} / {creds.get('monthly_quota', '?')} (剩余 / 月)\n"
        f"  scope     : {creds.get('scope', '?')}\n"
        f"  logged_at : {creds.get('logged_at', '?')}",
        title="ModelBridge 联网搜索状态", border_style="green",
    ))


@search_app.command("logout")
def cmd_logout() -> None:
    """清除联网搜索凭据。"""
    clear_credentials()
    console.print("[green]✓ 联网搜索凭据已清除[/green]")


@search_app.command("test")
def cmd_test(
    query: str = typer.Argument("ModelBridge", help="测试搜索的关键词。"),
    count: int = typer.Option(5, "--count", "-n", help="返回条数(1-50)。"),
) -> None:
    """加密调用一次 /v1/search,确认凭据与服务端连通。"""
    creds = load_credentials()
    if not creds:
        err_console.print("[red]未登录。先 `mbridge search login`。[/red]")
        raise typer.Exit(code=2)
    console.print(f"[grey]搜索「{query}」…[/grey]")
    try:
        result = do_search(
            creds["endpoint"],
            api_key=creds["api_key"],
            crypto_key=creds["crypto_key"],
            query=query,
            count=count,
        )
    except SearchError as exc:
        err_console.print(f"[red]搜索失败:{exc}[/red]")
        raise typer.Exit(code=1) from exc

    results = result.get("results") or []
    console.print(f"[green]✓ 返回 {len(results)} 条"
                  f"(本次消耗 {result.get('cost', '?')} 次配额,剩余 {result.get('remaining_quota', '?')})[/green]")
    for i, r in enumerate(results, 1):
        console.print(f"  [bold]{i}. {r.get('title', '(无标题)')}[/bold]")
        if r.get("url"):
            console.print(f"     [blue]{r['url']}[/blue]")
        if r.get("snippet"):
            console.print(f"     [grey]{r['snippet']}[/grey]")


__all__ = ["search_app"]
