"""Persist raw provider request/response pairs under ``~/.modelbridge/logs/``.

Used by ``--verbose`` paths so users can reproduce / file bugs against
specific provider behaviour without re-issuing real API calls.

Filenames look like ``2026-05-22_153000_deepseek-chat_chat.json``.

Never persist full API keys; bearer tokens in the request headers are
masked before writing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .utils import get_logs_dir, mask_secret, now_filestamp, now_iso


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def _safe_filename_component(s: str) -> str:
    return _SAFE_NAME_RE.sub("_", s)[:60] or "unknown"


def _scrub_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    scrubbed: dict[str, str] = {}
    for k, v in headers.items():
        lower = k.lower()
        if lower == "authorization" and isinstance(v, str):
            if v.startswith("Bearer "):
                scrubbed[k] = "Bearer " + mask_secret(v[len("Bearer ") :])
            else:
                scrubbed[k] = mask_secret(v)
        elif lower in {"x-api-key", "api-key", "x-token"}:
            scrubbed[k] = mask_secret(v)
        else:
            scrubbed[k] = v
    return scrubbed


def save_raw_exchange(
    *,
    model_name: str,
    provider: str,
    base_url: str,
    endpoint: str,
    request_headers: dict[str, str] | None,
    request_body: dict[str, Any] | None,
    response_status: int | None = None,
    response_raw: Any = None,
    error: dict[str, Any] | None = None,
    label: str = "chat",
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """Write a raw request/response record to disk.

    Returns the path on success, or ``None`` if the logs directory isn't
    writable (we never want telemetry to crash the CLI).
    """
    try:
        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    fname = (
        f"{now_filestamp()}_"
        f"{_safe_filename_component(model_name)}_"
        f"{_safe_filename_component(label)}.json"
    )
    path = logs_dir / fname

    msgs = (request_body or {}).get("messages") or []
    record: dict[str, Any] = {
        "ts": now_iso(),
        "label": label,
        "model_name": model_name,
        "provider": provider,
        "base_url": base_url,
        "endpoint": endpoint,
        "messages_count": len(msgs) if isinstance(msgs, list) else None,
        "request": {
            "headers": _scrub_headers(request_headers),
            "body": request_body,
        },
        "response": {
            "status_code": response_status,
            "raw": response_raw,
        },
    }
    if error:
        record["error"] = error
    if extra:
        record["extra"] = extra

    try:
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=_json_fallback),
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


def _json_fallback(o: Any) -> Any:
    # httpx headers, bytes, etc.
    try:
        return str(o)
    except Exception:
        return repr(o)
