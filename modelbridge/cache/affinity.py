"""Cache-affinity helpers — the model-switching side of prefix caching.

Doctrine (DeepSeek's own agent harness, cross-checked 2026-08): each
provider+model pair is an independent cache domain. Switching models —
even within one vendor, flash↔pro — never carries cached prefixes over,
and DeepSeek's wire format defines no body-level cache key. So the cache
levers ModelBridge actually owns are:

* prefix byte-stability — the same PromptBuilder bytes reach the provider
  from the REPL, ``mbridge ask``, and ``--route`` (see :mod:`router` paths);
* per-model hit telemetry — ``mbridge usage cache stats`` / ``/api/usage/cache``;
* an optional background warm-up of the new domain after ``/model``
  (``cache.warmup_on_switch``) — it pre-pays one miss-priced pass over the
  prefix to cut the next turn's first-token latency.

For endpoints that DO document a body-level cache key (e.g. OpenAI-style
``prompt_cache_key``), :func:`derive_cache_key` builds a stable key from
the prompt's stable-prefix hash. It is injected only on models that
declare the field name via ``models.yaml extra.cache_key_field`` — never
by default, and never for DeepSeek.
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import TYPE_CHECKING

from ..config import load_app_config

if TYPE_CHECKING:  # pragma: no cover - import-cycle guard, typing only
    from ..agent.session import Session
    from ..models import ModelEntry
    from ..schemas import ChatMessage

# OpenAI's prompt_cache_key caps at 64 chars; a hex digest prefix is safe
# for every documented charset ([a-zA-Z0-9_-]).
_MAX_KEY_LEN = 64
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_cache_key(key: str) -> str:
    """Force a key into the common ``[a-zA-Z0-9_-]{1,64}`` wire charset."""
    return _UNSAFE_CHARS.sub("_", key)[:_MAX_KEY_LEN]


def derive_cache_key(stable_prefix_hash: str) -> str:
    """Derive the affinity key for a prompt prefix.

    Deliberately independent of model and session: requests sharing the
    same stable prefix share the key, so switching models mid-conversation
    keeps the key stable (whether the provider can reuse anything across
    its cache domains is up to the provider — see module docstring).
    """
    digest = hashlib.sha256(
        f"mbridge:{stable_prefix_hash}".encode("utf-8")
    ).hexdigest()
    return sanitize_cache_key("mb-" + digest[:_MAX_KEY_LEN - 3])


def session_cache_key(session: "Session") -> str | None:
    """The affinity key for an agent session, or ``None`` when disabled.

    ``None`` means "send nothing" — callers put it straight on
    :class:`modelbridge.schemas.ChatRequest`, and provider adapters drop
    ``None`` keys without touching the request body.
    """
    key = (session.metadata or {}).get("cache_key")
    if not key:
        return None
    try:
        cfg = load_app_config()
        if not (cfg.cache.enabled and cfg.cache.affinity_key):
            return None
    except Exception:
        return None
    return sanitize_cache_key(str(key))


def warmup_after_model_switch(
    entry: "ModelEntry",
    messages: list["ChatMessage"],
    cache_key: str | None = None,
    *,
    timeout: float = 30.0,
) -> threading.Thread | None:
    """Pre-fill a freshly-switched model's cache domain in the background.

    One non-streaming ``max_tokens=4`` request over the current session
    messages: providers persist cache units at the request boundary, so the
    next real turn starts from a warm prefix instead of paying cold-start
    latency. Cost is one miss-priced pass over the prefix — the win is
    latency, not money, which is why this is opt-in
    (``cache.warmup_on_switch``). Fire-and-forget: every failure is
    swallowed into a debug log because a warm-up must never disturb the
    REPL. Returns the daemon thread (or ``None`` when it couldn't start).
    """

    def _run() -> None:
        try:
            from ..providers import get_provider
            from ..schemas import ChatRequest

            provider = get_provider(entry)
            req = ChatRequest(
                model=entry.model,
                messages=list(messages),
                max_tokens=4,
                cache_key=cache_key,
            )
            provider.chat(req, timeout=timeout, verbose_label="cache_warmup")
        except Exception as exc:  # noqa: BLE001 - warm-up must never raise
            from ..utils import get_logger

            try:
                get_logger().debug(
                    "cache warmup skipped after model switch: %s", exc
                )
            except Exception:
                pass

    try:
        t = threading.Thread(
            target=_run, name="mbridge-cache-warmup", daemon=True
        )
        t.start()
        return t
    except RuntimeError:
        # Can't even spawn a thread (interpreter shutting down) — give up
        # silently, same doctrine as above.
        return None


def cache_switch_note(
    old_entry: "ModelEntry | None", new_entry: "ModelEntry | None"
) -> str:
    """Human note about what a model switch does to prefix caches.

    Cache domains are per provider+model, so both "different vendor" and
    "same vendor, different model" mean a cold domain for the new model —
    the difference is only whether the OLD domain stays warm behind you.
    """
    if old_entry is None or new_entry is None:
        return ""
    if old_entry.name == new_entry.name:
        return ""
    same_vendor = (
        old_entry.provider == new_entry.provider
        and old_entry.base_url == new_entry.base_url
    )
    if same_vendor:
        return (
            "缓存提示：同厂商但模型已切换 —— 前缀缓存域按「厂商×模型」隔离，"
            "新模型不继承旧模型的缓存；切回原模型可恢复其缓存域。"
        )
    return (
        "缓存提示：已切换厂商 —— 前缀缓存域按「厂商×模型」隔离，"
        "新模型将从冷缓存开始。"
    )


__all__ = [
    "cache_switch_note",
    "derive_cache_key",
    "sanitize_cache_key",
    "session_cache_key",
    "warmup_after_model_switch",
]
