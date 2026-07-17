"""微信 iLink Bot 凭据持久化。存储在 ``~/.modelbridge/weixin.json``。

字段来自 ClawBot 登录接口 的 ``data.credentials``：

* ``bot_token``       — Bearer token，调用 iLink API 用。
* ``ilink_bot_id``    — Bot 标识符。
* ``ilink_user_id``   — 用户标识符。
* ``baseurl``         — 返回的 API 基础 URL（可能覆盖默认值）。
* ``logged_at``       — 本地写入时间戳。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..utils import get_app_dir


def _creds_file() -> Path:
    return get_app_dir() / "weixin.json"


def load_credentials() -> dict[str, Any] | None:
    p = _creds_file()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("bot_token"):
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
        # 限制权限 (POSIX; Windows 上 no-op，文件默认 ACL 用户私享)。
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


def get_bot_token() -> str | None:
    creds = load_credentials()
    return creds.get("bot_token") if creds else None


def get_baseurl() -> str:
    """返回登录时返回的 baseurl，没有则用默认 iLink 地址。"""
    creds = load_credentials()
    if creds and creds.get("baseurl"):
        return creds["baseurl"]
    from . import ILINK_BASE
    return ILINK_BASE
