"""ModelBridge 联网搜索 HTTP 客户端。

覆盖:OAuth PKCE 授权码流程(``authorize`` → 轮询 ``/oauth/wait/{state}`` →
换 token)、以及用 AES-256-GCM 加密调用 ``/v1/search``。风格对齐
``weixin/client.py``(同步 httpx + 统一错误处理),鉴权与加密按服务端契约实现。

服务端契约要点:
- 调用 ``/v1/search`` 鉴权用 ``mb-`` apikey(``Authorization: Bearer mb-xxx``),
  **不是** JWT access_token。
- mcp 渠道的 apikey 携带专属 ``crypto_key``;请求体需加密并声明
  ``X-MB-Enc: v1``,否则被降级为第三方计价。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from . import CLIENT_ID, SCOPE
from . import crypto as _crypto

DEFAULT_TIMEOUT = 30.0
SEARCH_TIMEOUT = 60.0  # 后端会重试 3 次 + 查 SearXNG,留足余量


class SearchError(RuntimeError):
    """联网搜索请求失败。附带 HTTP 状态码(如有)便于上层分流处理。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def pkce_pair() -> tuple[str, str]:
    """生成 PKCE ``(verifier, challenge)``,``code_challenge_method = S256``。"""
    verifier = secrets.token_urlsafe(64)  # 43-128 chars, urlsafe
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# OAuth 授权码流程
# ---------------------------------------------------------------------------

def build_authorize_url(
    endpoint: str,
    *,
    state: str,
    challenge: str,
    redirect_uri: str,
) -> str:
    """构造授权页 URL(``GET {endpoint}/oauth/authorize?...``)。"""
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return endpoint.rstrip("/") + "/oauth/authorize?" + urlencode(params)


def poll_state(endpoint: str, state: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """轮询授权状态。

    服务端契约:未就绪返回 **404** ``{"status":"pending"}``;就绪返回
    ``{"status":"ok","code":...,"api_key":...?}``;出错返回 ``{"status":"error",...}``。
    这里把 404 当作 pending,不当错误。
    """
    url = endpoint.rstrip("/") + f"/oauth/wait/{state}"
    try:
        resp = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as exc:
        raise SearchError(f"轮询授权状态网络错误: {exc}") from exc
    if resp.status_code == 404:
        return {"status": "pending"}
    if resp.status_code != 200:
        raise SearchError(
            f"轮询授权状态 HTTP {resp.status_code}: {resp.text[:300]}",
            status_code=resp.status_code,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SearchError(f"轮询授权状态返回非法 JSON: {resp.text[:300]}") from exc
    if not isinstance(payload, dict):
        raise SearchError("轮询授权状态返回非对象响应")
    return payload


def exchange_token(
    endpoint: str,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """用授权码换 token + apikey + crypto_key(``POST /oauth/token``,form-urlencoded)。"""
    url = endpoint.rstrip("/") + "/oauth/token"
    try:
        resp = httpx.post(
            url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SearchError(f"换取 token 网络错误: {exc}") from exc
    payload = _parse_json(resp)
    if resp.status_code != 200:
        raise SearchError(
            f"换取 token 失败 ({resp.status_code}): {_fmt_error(payload)}",
            status_code=resp.status_code,
        )
    if not payload.get("api_key"):
        raise SearchError(f"token 响应缺少 api_key: {payload}")
    return payload


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

def do_search(
    endpoint: str,
    *,
    api_key: str,
    crypto_key: str,
    query: str,
    count: int = 5,
    timeout: float = SEARCH_TIMEOUT,
    **extra: Any,
) -> dict[str, Any]:
    """加密调用 ``POST /v1/search``。

    返回服务端 ``SearchResponse`` 结构(已解密):``{query, results, cost, remaining_quota}``。
    """
    if not api_key or not crypto_key:
        raise SearchError("缺少 api_key 或 crypto_key;请先 `mbridge search login`。")

    plain: dict[str, Any] = {"q": query, "count": count}
    for key, value in extra.items():
        if value is not None:
            plain[key] = value
    envelope = _crypto.encrypt_dict(plain, crypto_key)

    url = endpoint.rstrip("/") + "/v1/search"
    try:
        resp = httpx.post(
            url,
            json=envelope,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-MB-Enc": "v1",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SearchError(f"搜索网络错误: {exc}") from exc

    # 错误响应(401/402/403/429/502 …)一般不加密,直接报错。
    if resp.status_code != 200:
        detail = _parse_json(resp)
        raise SearchError(
            _fmt_error(detail) or f"搜索失败 ({resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )

    payload = _parse_json(resp)
    # 成功响应在 mcp 渠道下是加密信封,需解密。
    if _crypto.is_envelope(payload):
        payload = _crypto.decrypt_dict(payload, crypto_key)
    return payload


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError:
        return {"detail": resp.text[:300]}
    return payload if isinstance(payload, dict) else {"detail": str(payload)[:300]}


def _fmt_error(payload: dict[str, Any]) -> str:
    """从 FastAPI / OAuth 错误体里提取人可读信息。"""
    for key in ("detail", "error_description", "error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    if payload:
        return str(payload)[:300]
    return ""
