"""MCP structured logging.

Follows ``raw_logger``'s three rules: persist for reproducibility, scrub
secrets, and never let logging crash the caller. Two surfaces:

* :func:`mcp_logger` — a child of the project's rotating file logger
  (``~/.modelbridge/logs/mbridge.log``) for lifecycle + tool-call metadata.
* :func:`save_mcp_frame` — optional full-frame dump under
  ``~/.modelbridge/logs/mcp/<server_id>/`` (only when ``verbose=True``).

Secret scrubbing reuses :func:`modelbridge.utils.mask_secret` and masks any
key that *looks* like a credential (``*_KEY`` / ``*_TOKEN`` / ``authorization``).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..utils import get_logger, get_logs_dir, mask_secret, now_filestamp, now_iso

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")
_SECRET_KEY_RE = re.compile(r"(key|token|secret|password|authorization)", re.IGNORECASE)

_mcp_logger: logging.Logger | None = None


def mcp_logger() -> logging.Logger:
    """Return the ``modelbridge.mcp`` child logger (shares the rotating file)."""
    global _mcp_logger
    if _mcp_logger is None:
        # Ensure the parent handler/file is configured, then take a child.
        get_logger()
        _mcp_logger = logging.getLogger("modelbridge.mcp")
    return _mcp_logger


def _safe(name: str) -> str:
    return _SAFE_NAME_RE.sub("_", name)[:60] or "unknown"


def scrub(value: Any) -> Any:
    """Recursively mask secret-looking string values in a dict/list."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, str) and _SECRET_KEY_RE.search(str(k)):
                out[k] = mask_secret(v)
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def scrub_env(env: dict[str, str] | None) -> dict[str, str]:
    if not env:
        return {}
    return {
        k: (mask_secret(v) if _SECRET_KEY_RE.search(k) else v) for k, v in env.items()
    }


def save_mcp_frame(
    *,
    server_id: str,
    direction: str,  # "out" | "in"
    method: str,
    frame: dict[str, Any],
) -> Path | None:
    """Dump a single JSON-RPC frame to disk. Returns the path, or None on error.

    Only called on the verbose path. Never raises.
    """
    try:
        d = get_logs_dir() / "mcp" / _safe(server_id)
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    fname = f"{now_filestamp()}_{direction}_{_safe(method)}.json"
    path = d / fname
    record = {
        "ts": now_iso(),
        "server_id": server_id,
        "direction": direction,
        "method": method,
        "frame": scrub(frame),
    }
    try:
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


def log_tool_call(
    *,
    server_id: str,
    tool: str,
    correlation_id: str,
    elapsed_ms: int,
    is_error: bool,
    result_chars: int,
) -> None:
    """Structured one-line record of a completed tools/call."""
    try:
        mcp_logger().info(
            "mcp.tool_call server=%s tool=%s cid=%s ms=%d error=%s out_chars=%d",
            server_id, tool, correlation_id, elapsed_ms, is_error, result_chars,
        )
    except Exception:
        pass


def log_lifecycle(server_id: str, event: str, detail: str = "") -> None:
    try:
        mcp_logger().info("mcp.lifecycle server=%s event=%s %s", server_id, event, detail)
    except Exception:
        pass


__all__ = [
    "mcp_logger",
    "scrub",
    "scrub_env",
    "save_mcp_frame",
    "log_tool_call",
    "log_lifecycle",
]
