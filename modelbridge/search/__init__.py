"""ModelBridge 联网搜索客户端。

通过 OAuth(``mbridge-cli`` 公共客户端 + PKCE)登录到 ModelBridge 服务端,
拿到 ``mb-`` apikey 与专属 ``crypto_key``,本地调用服务端 ``/v1/search``
做收费联网搜索。凭据存于 ``~/.modelbridge/search.json``。

对外入口::

    mbridge search login    OAuth 授权拿 key
    mbridge search status   查看套餐 / 配额
    mbridge search logout   清除凭据
    mbridge search test     ping 一次搜索

Agent 侧:已登录时 ``WebSearchTool`` 自动注册(见 :mod:`modelbridge.search.wiring`),
REPL / 微信网关 / 浏览器侧边栏三通道统一注入。
"""
from __future__ import annotations

import os

# 服务端默认地址。可用环境变量 MODELBRIDGE_SERVER 覆盖。
DEFAULT_SERVER = "https://web.crisxie.top"

# OAuth 公共客户端(服务端预置,公共客户端无需 client_secret)。
CLIENT_ID = "mbridge-cli"
SCOPE = "search:read chat:read"

# 授权码在服务端暂存 10 分钟,本地轮询留足余量。
OAUTH_MAX_WAIT = 600.0


def default_server() -> str:
    """服务端地址:环境变量 ``MODELBRIDGE_SERVER`` 优先,否则用默认。"""
    return (os.environ.get("MODELBRIDGE_SERVER") or DEFAULT_SERVER).rstrip("/")


__all__ = ["DEFAULT_SERVER", "CLIENT_ID", "SCOPE", "OAUTH_MAX_WAIT", "default_server"]
