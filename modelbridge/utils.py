"""Utility helpers: paths, secret masking, logging, time."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
# Time
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_filestamp() -> str:
    """Filesystem-safe timestamp used for raw log filenames."""
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")
