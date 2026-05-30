"""Unit tests for encrypt-at-rest of API keys.

Never touches the real OS credential vault: the keyring backend is stubbed
(fake in-memory vault) and the Fernet path is redirected to a ``tmp_path``
key file. Covers protect/reveal round-trips for both backends, legacy
plaintext passthrough, idempotency, and empty input.
"""

from __future__ import annotations

import modelbridge.secrets as secrets
from modelbridge.secrets import is_protected, protect, reveal


def test_is_protected():
    assert is_protected("keyring:x")
    assert is_protected("enc:abc")
    assert not is_protected("plain-key")
    assert not is_protected("")
    assert not is_protected(None)


def test_fernet_round_trip(monkeypatch, tmp_path):
    # Force the keyring backend to be unavailable → Fernet fallback.
    monkeypatch.setattr(secrets, "_keyring_set", lambda name, secret: False)
    monkeypatch.setattr(secrets, "get_app_dir", lambda: tmp_path)

    token = protect("m", "sk-secret-123")
    assert token.startswith("enc:")
    assert (tmp_path / "secret.key").exists()
    assert reveal("m", token) == "sk-secret-123"


def test_keyring_round_trip(monkeypatch):
    vault: dict[str, str] = {}
    monkeypatch.setattr(
        secrets, "_keyring_set",
        lambda name, secret: (vault.__setitem__(name, secret) or True),
    )
    monkeypatch.setattr(secrets, "_keyring_get", lambda name: vault.get(name))

    token = protect("mymodel", "sk-abc")
    assert token == "keyring:mymodel"
    assert vault == {"mymodel": "sk-abc"}  # secret left the YAML, lives in vault
    assert reveal("mymodel", token) == "sk-abc"


def test_legacy_plaintext_passthrough():
    # An un-marked value is treated as a legacy plaintext key.
    assert reveal("m", "sk-legacy") == "sk-legacy"


def test_empty_input():
    assert protect("m", "") == ""
    assert protect("m", None) == ""
    assert reveal("m", "") == ""
    assert reveal("m", None) == ""


def test_protect_is_idempotent():
    # Already-protected tokens are returned unchanged (no double-wrapping).
    assert protect("m", "keyring:m") == "keyring:m"
    assert protect("m", "enc:xyz") == "enc:xyz"
