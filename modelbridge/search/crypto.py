"""客户端 AES-256-GCM 加密 —— 与服务端 ``app/core/crypto.py`` 逐字节对齐。

密钥来源:OAuth 下发的专属 ``crypto_key``,经 HKDF-SHA256 派生为 32B AES 密钥。
mcp 渠道的 apikey 不声明加密会被服务端降级为第三方计价,故本模块必须启用。

⚠️ 关键:服务端用的是**手写 HKDF**(extract/expand 两步 hmac),并非
cryptography 库的 ``HKDF`` 类——两者输出不同。本模块的 :func:`derive_key`
必须逐字节复制服务端 ``_derive_key``,否则双方密钥不一致、解密全部失败。

信封格式(JSON-RPC / HTTP 兼容)::

    {"enc": "v1", "alg": "aes-256-gcm",
     "iv": "<base64url 12B nonce>", "ct": "<base64url 密文 + 16B GCM tag>"}
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_INFO = b"modelbridge-search-v1"
_ENC_VERSION = "v1"
_ALG = "aes-256-gcm"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def derive_key(crypto_key: str) -> bytes:
    """从 ``crypto_key`` 派生 32B AES-256 密钥(逐字节对齐服务端 ``_derive_key``)。"""
    # Extract
    prk = hmac.new(b"\x00" * 32, crypto_key.encode(), hashlib.sha256).digest()
    # Expand(单段)
    okm = hmac.new(prk, _INFO + b"\x01", hashlib.sha256).digest()
    return okm[:32]


def _dumps(obj: dict) -> bytes:
    # 与服务端一致:紧凑、非 ASCII 不转义。
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def is_envelope(obj: object) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("enc") == _ENC_VERSION
        and obj.get("alg") == _ALG
    )


def encrypt_dict(plain: dict, crypto_key: str) -> dict:
    """加密一个 dict,返回加密信封。"""
    aesgcm = AESGCM(derive_key(crypto_key))
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, _dumps(plain), None)  # 末尾含 16B GCM tag
    return {"enc": _ENC_VERSION, "alg": _ALG, "iv": _b64e(nonce), "ct": _b64e(ct)}


def decrypt_dict(envelope: dict, crypto_key: str) -> dict:
    """解密对称加密信封。非加密格式(无 ``enc`` 字段)原样返回,兼容明文响应。"""
    if not is_envelope(envelope):
        return envelope
    aesgcm = AESGCM(derive_key(crypto_key))
    nonce = _b64d(envelope["iv"])
    ct = _b64d(envelope["ct"])
    plaintext = aesgcm.decrypt(nonce, ct, None)  # 校验失败抛 InvalidTag
    return json.loads(plaintext.decode("utf-8"))
