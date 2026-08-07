"""ModelBridge 联网搜索凭据持久化。存储在 ``~/.modelbridge/search.json``。

字段来自服务端 OAuth token 响应(``OAuthTokenOut``):

* ``endpoint``        — 服务端基础地址(如 https://web.crisxie.top)。
* ``access_token``    — JWT,访问 ``/v1/me`` 等 cookie/JWT 路径用(调搜索不用它)。
* ``refresh_token``   — 刷新 access_token 用。
* ``expires_at``      — access_token 过期 unix 时间戳(可能为空)。
* ``api_key``         — ``mb-`` 前缀,调用 ``/v1/search`` 与 ``/mcp`` 用(长期有效)。
* ``crypto_key``      — 专属 AES-GCM 派生密钥(token_urlsafe(32))。
* ``plan_code`` / ``plan_name``    — 套餐。
* ``monthly_quota`` / ``used_quota`` / ``remaining`` — 配额快照。
* ``scope``           — 授权范围。
* ``logged_at``       — 本地写入时间戳。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..utils import get_app_dir


def _creds_file() -> Path:
    return get_app_dir() / "search.json"


def load_credentials() -> dict[str, Any] | None:
    p = _creds_file()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("api_key"):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_credentials(creds: dict[str, Any]) -> Path:
    p = _creds_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(creds)
    out.setdefault("logged_at", int(time.time()))
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        # 限制权限(POSIX;Windows 上 no-op,文件默认 ACL 用户私享)。
        import os

        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def clear_credentials() -> None:
    p = _creds_file()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def get_endpoint() -> str:
    """已登录则用登录时的服务端地址,否则回退默认。"""
    creds = load_credentials()
    if creds and creds.get("endpoint"):
        return str(creds["endpoint"]).rstrip("/")
    from . import default_server

    return default_server()


def apikey_fingerprint(api_key: str | None) -> str:
    """``mb-xxxxxxxx…xxxx`` 风格指纹,用于安全展示(绝不打印明文)。"""
    if not api_key:
        return "<empty>"
    prefix = "mb-"
    body = api_key[len(prefix):] if api_key.startswith(prefix) else api_key
    if len(body) <= 8:
        return prefix + "*" * len(body)
    return f"{prefix}{body[:4]}…{body[-4:]}"
