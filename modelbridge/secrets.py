"""Encrypt-at-rest for API keys.

``models.yaml`` historically stored ``api_key`` in plaintext. This module
moves the secret off the YAML file:

* **keyring (preferred)** — store the secret in the OS credential vault
  (Windows Credential Manager / macOS Keychain / Linux Secret Service).
  ``models.yaml`` then only holds the marker ``keyring:<name>``.
* **Fernet (fallback)** — if keyring has no usable backend, symmetric-encrypt
  with a machine-local key at ``~/.modelbridge/secret.key`` (chmod 600). The
  YAML stores ``enc:<token>``.
* **plaintext (last resort)** — if neither ``keyring`` nor ``cryptography`` is
  installed, the value is left as-is and a warning is logged. Nothing breaks;
  it's just no more secure than before.

``protect`` / ``reveal`` are inverses. Both are no-ops on empty input and on
values that are already an env-var reference handled elsewhere — they only
deal with the literal ``api_key`` field.
"""

from __future__ import annotations

import logging
import os

from .utils import get_app_dir

log = logging.getLogger(__name__)

SERVICE = "modelbridge"
KEYRING_MARKER = "keyring:"
ENC_MARKER = "enc:"


def is_protected(stored: str | None) -> bool:
    """True if ``stored`` is a keyring reference or Fernet ciphertext."""
    return bool(stored) and stored.startswith((KEYRING_MARKER, ENC_MARKER))


# ---------------------------------------------------------------------------
# keyring backend
# ---------------------------------------------------------------------------

def _keyring_set(name: str, secret: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE, name, secret)
        return True
    except Exception as e:  # noqa: BLE001 - any backend error → fall back
        log.debug("keyring set failed for %s: %s", name, e)
        return False


def _keyring_get(name: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, name)
    except Exception as e:  # noqa: BLE001
        log.debug("keyring get failed for %s: %s", name, e)
        return None


# ---------------------------------------------------------------------------
# Fernet backend (machine-local symmetric key)
# ---------------------------------------------------------------------------

def _fernet():
    """Return a ``Fernet`` instance, creating the local key if needed; or None."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    path = get_app_dir() / "secret.key"
    try:
        if path.exists():
            key = path.read_bytes()
        else:
            key = Fernet.generate_key()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass  # best-effort on platforms without POSIX perms
        return Fernet(key)
    except OSError as e:
        log.debug("fernet key io failed: %s", e)
        return None


def _fernet_encrypt(secret: str) -> str | None:
    f = _fernet()
    if f is None:
        return None
    return f.encrypt(secret.encode("utf-8")).decode("ascii")


def _fernet_decrypt(token: str) -> str | None:
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as e:  # noqa: BLE001 - InvalidToken etc.
        log.debug("fernet decrypt failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def protect(name: str, secret: str | None) -> str:
    """Store ``secret`` securely and return the token to persist in YAML.

    Returns ``""`` for empty input, the value unchanged if it's already
    protected, and the original plaintext (with a warning) if no backend is
    available.
    """
    if not secret:
        return ""
    if is_protected(secret):
        return secret

    if _keyring_set(name, secret):
        return f"{KEYRING_MARKER}{name}"

    token = _fernet_encrypt(secret)
    if token is not None:
        return f"{ENC_MARKER}{token}"

    log.warning(
        "无法加密 api_key '%s'（keyring 与 cryptography 均不可用），仍以明文保存。"
        "建议 `pip install keyring cryptography`，或改用 api_key_env 环境变量。",
        name,
    )
    return secret


def reveal(name: str, stored: str | None) -> str:
    """Inverse of :func:`protect`. Returns the plaintext secret (or "")."""
    if not stored:
        return ""
    if stored.startswith(KEYRING_MARKER):
        ref = stored[len(KEYRING_MARKER):] or name
        return _keyring_get(ref) or ""
    if stored.startswith(ENC_MARKER):
        return _fernet_decrypt(stored[len(ENC_MARKER):]) or ""
    return stored  # legacy plaintext
