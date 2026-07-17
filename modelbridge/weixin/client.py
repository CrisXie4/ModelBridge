"""HTTP client for the WeChat iLink Bot API.

Supports QR login, cursor-based long polling, text messages, typing state,
and encrypted CDN media upload/download. Both direct iLink responses and legacy
{"success": true, "data": {...}} wrappers are accepted.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import secrets
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from . import ILINK_BASE, ILINK_CDN

DEFAULT_TIMEOUT = 45.0
_CLIENT_VERSION = "8.0.70"
_CHANNEL_VERSION = "1.0.0"
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_VIDEO = 4
ITEM_FILE = 5
MESSAGE_USER = 1
MESSAGE_BOT = 2
MESSAGE_STATE_FINISH = 2


class WeixinError(RuntimeError):
    """Raised when an iLink request fails."""


class WeixinTransientError(WeixinError):
    """A benign long-poll hiccup: the server closed the held connection or the
    read timed out mid-hold. Not a real failure — the caller should just
    reconnect immediately without backoff or scary logging."""


def _random_uin() -> str:
    """Return base64(decimal random uint32), as required by iLink."""
    raw = str(secrets.randbits(32)).encode("ascii")
    return base64.b64encode(raw).decode("ascii")


def _headers(bot_token: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "iLink-App-ClientVersion": _CLIENT_VERSION,
        "X-WECHAT-UIN": _random_uin(),
    }
    if bot_token:
        headers["Authorization"] = f"Bearer {bot_token}"
    return headers


def _json_response(response: httpx.Response, operation: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise WeixinError(
            f"{operation} HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise WeixinError(f"{operation} returned invalid JSON: {response.text[:300]}") from exc
    if not isinstance(payload, dict):
        raise WeixinError(f"{operation} returned {type(payload).__name__}, expected object")
    if payload.get("success") is False:
        raise WeixinError(f"{operation} failed: {payload}")
    ret = payload.get("ret")
    if ret not in (None, 0, "0"):
        raise WeixinError(f"{operation} ret={ret}: {payload.get('errmsg') or payload}")
    data = payload.get("data")
    if payload.get("success") is True and isinstance(data, dict):
        return data
    return payload


def _normalise_baseurl(value: str | None) -> str:
    return (value or ILINK_BASE).strip().rstrip("/")


def _decode_aes_key(value: str) -> bytes:
    """Accept base64(raw key), base64(hex key), or direct hex."""
    value = (value or "").strip()
    if not value:
        raise WeixinError("media object has no aes_key")
    if len(value) == 32:
        try:
            return bytes.fromhex(value)
        except ValueError:
            pass
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise WeixinError("media aes_key is not valid base64/hex") from exc
    if len(decoded) == 16:
        return decoded
    try:
        text = decoded.decode("ascii")
        if len(text) == 32:
            return bytes.fromhex(text)
    except (UnicodeDecodeError, ValueError):
        pass
    raise WeixinError(f"unexpected media aes_key length: {len(decoded)}")


class WeixinClient:
    """Stateful iLink client. Reuse one instance to preserve update cursors."""

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
        baseurl: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if bot_token is None:
            from .creds import get_bot_token

            bot_token = get_bot_token()
        if baseurl is None:
            from .creds import get_baseurl

            baseurl = get_baseurl()
        self.bot_token = bot_token or ""
        self.bot_id = bot_id or ""
        self.user_id = user_id or ""
        self.baseurl = _normalise_baseurl(baseurl)
        self._http = http_client or httpx.Client()
        self._owns_http = http_client is None
        self._updates_cursor = ""
        self.last_longpoll_timeout_ms: int | None = None
        self._typing_tickets: dict[str, str] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    @staticmethod
    def fetch_qrcode(
        *,
        baseurl: str | None = None,
        bot_type: int = 3,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Fetch a QR token and displayable QR content."""
        url = _normalise_baseurl(baseurl) + "/ilink/bot/get_bot_qrcode"
        try:
            response = httpx.get(url, params={"bot_type": bot_type}, timeout=timeout)
        except httpx.HTTPError as exc:
            raise WeixinError(f"get_bot_qrcode network error: {exc}") from exc
        payload = _json_response(response, "get_bot_qrcode")
        qrcode = str(payload.get("qrcode") or "")
        content = str(
            payload.get("qrcode_img_content")
            or payload.get("qrcode_url")
            or payload.get("url")
            or ""
        )
        if not qrcode:
            raise WeixinError(f"get_bot_qrcode returned no qrcode: {payload}")
        return {
            "qrcode": qrcode,
            "qrcode_img_content": content,
            "qrcode_url": content,
        }

    @staticmethod
    def poll_qrcode_status(
        qrcode: str,
        *,
        baseurl: str | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Poll wait/scaned/confirmed/expired QR state."""
        url = _normalise_baseurl(baseurl) + "/ilink/bot/get_qrcode_status"
        try:
            response = httpx.get(url, params={"qrcode": qrcode}, timeout=timeout)
        except httpx.HTTPError as exc:
            raise WeixinError(f"get_qrcode_status network error: {exc}") from exc
        payload = _json_response(response, "get_qrcode_status")
        result = dict(payload)
        status = str(result.get("status") or "wait").lower()
        result["status"] = "scaned" if status == "scanned" else status
        if result["status"] == "confirmed":
            nested = result.get("credentials")
            credentials = dict(nested) if isinstance(nested, dict) else {}
            for key in ("bot_token", "ilink_bot_id", "ilink_user_id"):
                if result.get(key) and not credentials.get(key):
                    credentials[key] = result[key]
            result["credentials"] = credentials
        return result

    def _post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        timeout: float,
        operation: str,
    ) -> dict[str, Any]:
        if not self.bot_token:
            raise WeixinError("missing bot_token; run mbridge weixin login first")
        try:
            response = self._http.post(
                self.baseurl + path,
                json=dict(body),
                headers=_headers(self.bot_token),
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            # 长轮询里，服务端到点会关掉挂起的连接（而不是回空包），或读超时——
            # 这些是正常现象，标成 transient 让调用方安静重连，别当故障退避。
            if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ReadError)):
                raise WeixinTransientError(f"{operation} transient: {exc}") from exc
            raise WeixinError(f"{operation} network error: {exc}") from exc
        return _json_response(response, operation)

    def get_updates(self, *, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
        """Long-poll messages and advance get_updates_buf."""
        payload = self._post(
            "/ilink/bot/getupdates",
            {
                "get_updates_buf": self._updates_cursor,
                "base_info": {"channel_version": _CHANNEL_VERSION},
            },
            timeout=timeout,
            operation="getupdates",
        )
        cursor = payload.get("get_updates_buf")
        if isinstance(cursor, str):
            self._updates_cursor = cursor
        longpoll = payload.get("longpolling_timeout_ms")
        if isinstance(longpoll, (int, float)):
            self.last_longpoll_timeout_ms = int(longpoll)
        messages = payload.get("msgs")
        if not isinstance(messages, list):
            messages = payload.get("updates")
        if not isinstance(messages, list):
            messages = []
        return [message for message in messages if isinstance(message, dict)]

    def send_message(
        self,
        *,
        to: str | None = None,
        text: str,
        context_token: str | None,
        msg_type: str = "text",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send one text message using the original context token."""
        if msg_type != "text":
            raise WeixinError("send_message only accepts text; use send_media_message")
        if not context_token:
            raise WeixinError("sendmessage requires context_token")
        if not to:
            raise WeixinError("sendmessage requires to_user_id")
        message = {
            "from_user_id": self.bot_id,
            "to_user_id": to,
            "client_id": str(uuid.uuid4()),
            "message_type": MESSAGE_BOT,
            "message_state": MESSAGE_STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
            "context_token": context_token,
        }
        return self._post(
            "/ilink/bot/sendmessage",
            {"msg": message, "base_info": {"channel_version": _CHANNEL_VERSION}},
            timeout=timeout,
            operation="sendmessage",
        )

    def send_media_message(
        self,
        *,
        to: str,
        context_token: str,
        item: Mapping[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send a pre-built iLink image/file/video item."""
        message = {
            "from_user_id": self.bot_id,
            "to_user_id": to,
            "client_id": str(uuid.uuid4()),
            "message_type": MESSAGE_BOT,
            "message_state": MESSAGE_STATE_FINISH,
            "item_list": [dict(item)],
            "context_token": context_token,
        }
        return self._post(
            "/ilink/bot/sendmessage",
            {"msg": message, "base_info": {"channel_version": _CHANNEL_VERSION}},
            timeout=timeout,
            operation="sendmessage",
        )

    def get_config(
        self,
        *,
        context_token: str | None = None,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"ilink_user_id": self.user_id}
        if context_token:
            body["context_token"] = context_token
        return self._post(
            "/ilink/bot/getconfig", body, timeout=timeout, operation="getconfig"
        )

    def send_typing(
        self,
        *,
        context_token: str | None,
        status: int = 1,
        timeout: float = 15.0,
    ) -> bool:
        """Send typing state; fetch and cache typing_ticket when necessary."""
        if not context_token or not self.user_id:
            return False
        ticket = self._typing_tickets.get(context_token)
        try:
            if not ticket:
                config = self.get_config(context_token=context_token, timeout=timeout)
                ticket = str(config.get("typing_ticket") or "")
                if not ticket:
                    return False
                self._typing_tickets[context_token] = ticket
            self._post(
                "/ilink/bot/sendtyping",
                {
                    "ilink_user_id": self.user_id,
                    "typing_ticket": ticket,
                    "status": status,
                },
                timeout=timeout,
                operation="sendtyping",
            )
            return True
        except WeixinError:
            self._typing_tickets.pop(context_token, None)
            return False

    def get_upload_url(
        self,
        *,
        filekey: str,
        media_type: int,
        to_user_id: str,
        raw_size: int,
        raw_md5: str,
        encrypted_size: int,
        aes_key: bytes,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        payload = self._post(
            "/ilink/bot/getuploadurl",
            {
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": encrypted_size,
                "no_need_thumb": True,
                "aeskey": base64.b64encode(aes_key).decode("ascii"),
            },
            timeout=timeout,
            operation="getuploadurl",
        )
        if not payload.get("upload_url"):
            raise WeixinError(f"getuploadurl returned no upload_url: {payload}")
        return payload

    def upload_media(
        self,
        source: str | Path | bytes,
        *,
        media_type: int,
        to_user_id: str,
        filename: str | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """AES-128-ECB encrypt media and PUT it to the returned CDN URL."""
        if isinstance(source, bytes):
            raw = source
            name = filename or "upload.bin"
        else:
            path = Path(source)
            raw = path.read_bytes()
            name = filename or path.name
        aes_key = secrets.token_bytes(16)
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        padder = padding.PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        filekey = uuid.uuid4().hex
        upload_info = self.get_upload_url(
            filekey=filekey,
            media_type=media_type,
            to_user_id=to_user_id,
            raw_size=len(raw),
            raw_md5=hashlib.md5(raw).hexdigest(),  # noqa: S324 - protocol checksum
            encrypted_size=len(encrypted),
            aes_key=aes_key,
            timeout=min(timeout, 30.0),
        )
        try:
            response = self._http.put(
                str(upload_info["upload_url"]),
                content=encrypted,
                headers={"Content-Type": "application/octet-stream"},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise WeixinError(f"CDN upload failed: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise WeixinError(f"CDN upload HTTP {response.status_code}: {response.text[:300]}")
        return {
            **upload_info,
            "filekey": filekey,
            "filename": name,
            "raw_size": len(raw),
            "encrypted_size": len(encrypted),
            "aes_key": base64.b64encode(aes_key).decode("ascii"),
        }

    def download_media(
        self,
        media: Mapping[str, Any],
        *,
        timeout: float = 30.0,
        max_bytes: int = _MAX_DOWNLOAD_BYTES,
    ) -> bytes:
        """Download and decrypt an incoming iLink media object."""
        direct_url = str(media.get("full_url") or media.get("url") or "")
        encrypt_query = str(media.get("encrypt_query_param") or "")
        if direct_url:
            url = direct_url
        elif encrypt_query:
            url = f"{ILINK_CDN}/download?encrypted_query_param={quote(encrypt_query)}"
        else:
            raise WeixinError("media object has no download URL")
        try:
            response = self._http.get(url, timeout=timeout)
        except httpx.HTTPError as exc:
            raise WeixinError(f"media download failed: {exc}") from exc
        if response.status_code != 200:
            raise WeixinError(f"media download HTTP {response.status_code}")
        encrypted = response.content
        if len(encrypted) > max_bytes:
            raise WeixinError(f"media exceeds size limit ({len(encrypted)} > {max_bytes})")
        key = _decode_aes_key(str(media.get("aes_key") or media.get("aeskey") or ""))
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        try:
            return unpadder.update(padded) + unpadder.finalize()
        except ValueError as exc:
            raise WeixinError("media decryption failed") from exc

    @staticmethod
    def guess_mime(filename: str | None, data: bytes) -> str:
        mime = mimetypes.guess_type(filename or "")[0]
        if mime:
            return mime
        if data.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
            return "image/png"
        if data.startswith(b"\\xff\\xd8\\xff"):
            return "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        return "application/octet-stream"
