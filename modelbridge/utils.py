"""Utility helpers: paths, secret masking, logging, time."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# Atomic file writes
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically (temp file in the same dir +
    ``os.replace``).

    A crash / Ctrl-C / power loss mid-write can never leave a truncated
    file: a concurrent reader sees either the old content or the new, never
    a torn half. ``os.replace`` is atomic on the same volume on both POSIX
    and Windows. This matters for state files like ``cache_stats.json``
    whose corruption would otherwise silently lose provider-cache savings.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR_NAME = ".modelbridge"
LEGACY_APP_DIR_NAME = ".cnagent"  # v0.1 layout — read as fallback if present
CONFIG_FILE_NAME = "config.yaml"
MODELS_FILE_NAME = "models.yaml"
LOGS_DIR_NAME = "logs"


def get_home_dir() -> Path:
    """Return the user's home directory (cross-platform)."""
    return Path(os.path.expanduser("~"))


def get_app_dir() -> Path:
    """Return ``~/.modelbridge``.

    Resolution order:

    1. ``MBRIDGE_HOME`` environment override (preferred).
    2. ``CNAGENT_HOME`` legacy override (still honoured).
    3. ``~/.modelbridge`` if it exists.
    4. ``~/.cnagent`` if it exists (v0.1 layout).
    5. ``~/.modelbridge`` (default — created lazily by ``mbridge init``).
    """
    override = os.environ.get("MBRIDGE_HOME") or os.environ.get("CNAGENT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    home = get_home_dir()
    new = home / APP_DIR_NAME
    legacy = home / LEGACY_APP_DIR_NAME
    if new.exists():
        return new
    if legacy.exists():
        return legacy
    return new


def get_config_path() -> Path:
    return get_app_dir() / CONFIG_FILE_NAME


def get_models_path() -> Path:
    return get_app_dir() / MODELS_FILE_NAME


def get_logs_dir() -> Path:
    return get_app_dir() / LOGS_DIR_NAME


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Mask an API key for safe display."""
    if not value:
        return "<empty>"
    if value == "EMPTY":
        return "EMPTY"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def resolve_api_key(api_key: str | None, api_key_env: str | None) -> str:
    """Resolve API key with priority: explicit value > environment variable.

    Returns an empty string if neither is set (caller decides if that is OK,
    e.g. local Ollama models).
    """
    if api_key:
        return api_key
    if api_key_env:
        return os.environ.get(api_key_env, "") or ""
    return ""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOGGER_NAME = "modelbridge"
_logger_configured = False


def get_logger() -> logging.Logger:
    """Lazy rotating file logger at ``~/.modelbridge/logs/mbridge.log``."""
    global _logger_configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _logger_configured:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            logs_dir / "mbridge.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())

    _logger_configured = True
    return logger


# ---------------------------------------------------------------------------
# Runtime debug toggle (/debug on|off)
# ---------------------------------------------------------------------------

_debug_enabled = False


def is_debug() -> bool:
    """True if verbose debug logging is currently enabled."""
    return _debug_enabled


def set_debug(enabled: bool) -> Path | None:
    """Toggle file logging at runtime (wired to the ``/debug`` command).

    ``enabled=True``  → ensure the rotating file logger is configured and
    emit ``DEBUG``-level records to ``~/.modelbridge/logs/mbridge.log``.
    ``enabled=False`` → silence the logger so it stops writing any records.

    Returns the log file path when enabling, else ``None``.
    """
    global _debug_enabled
    logger = get_logger()
    if enabled:
        logger.disabled = False
        logger.setLevel(logging.DEBUG)
        _debug_enabled = True
        logger.debug("debug logging enabled")
        return get_logs_dir() / "mbridge.log"
    # Record the transition before muting, then mute.
    logger.info("debug logging disabled")
    logger.setLevel(logging.INFO)
    logger.disabled = True
    _debug_enabled = False
    return None


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_filestamp() -> str:
    """Filesystem-safe timestamp used for raw log filenames."""
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")
