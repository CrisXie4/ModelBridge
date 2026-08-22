"""Utility helpers: paths, secret masking, logging, time."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# Atomic file writes
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8", sync: bool = True) -> None:
    """Write ``text`` to ``path`` atomically (temp file in the same dir +
    ``os.replace``).

    A crash / Ctrl-C / power loss mid-write can never leave a truncated
    file: a concurrent reader sees either the old content or the new, never
    a torn half. ``os.replace`` is atomic on the same volume on both POSIX
    and Windows. This matters for state files like ``cache_stats.json``
    whose corruption would otherwise silently lose provider-cache savings.

    ``sync=False`` skips the ``fsync`` before replace: on Windows (with
    antivirus scanning every temp file) an fsync costs ~10ms+ per write, so
    high-frequency ephemeral state (cache stats, live.json) opts out —
    those files keep atomic-replace durability and simply risk losing the
    last write on an OS crash, which is acceptable for counters/UI state.
    Real configuration keeps the fsync.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            if sync:
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


# ---------------------------------------------------------------------------
# LLM safety judgement (shared)
# ---------------------------------------------------------------------------

def llm_safety_judge(
    *, tool: str, summary: str, detail: str, reason: str = "",
) -> tuple[bool, str]:
    """Ask a tiny/cheap model whether a browser action is safe to auto-approve.

    Single shared implementation for the CLI REPL, the bridge side-panel and
    the weixin gateway — the judgement prompt and verdict parsing must stay
    identical across channels, so they live in exactly one place.

    Returns ``(is_safe, judge_reason)``. On any failure (no model available,
    provider error, timeout, unparseable verdict) returns ``(False, reason)``
    so callers fail closed to manual approval — never auto-approve on a
    judge error.
    """
    try:
        from .providers import get_provider
        from .config import load_app_config, load_models_file, find_model
        from .schemas import ChatMessage, ChatRequest

        reason_line = f"\n意图: {reason}" if reason else ""
        prompt = (
            f"判断以下浏览器操作是否安全可自动同意。\n"
            f"工具: {tool}\n操作: {summary}{reason_line}\n详情: {detail[:300]}\n\n"
            f"判定「安全」的标准：后果可控可撤销，或属于常规低风险操作"
            f"（清缓存、关弹窗、取消订阅、登出、滚动、筛选、展开折叠、翻页、"
            f"同意 cookie 等）。涉及支付/转账/删除账户/提交订单/修改密码/"
            f"发送消息/同意条款 → 「不安全」。\n\n"
            f"先用一两句话给理由，最后一行严格按此格式输出结论，不要加别的字：\n"
            f"结论：安全\n"
            f"或\n"
            f"结论：不安全\n"
        )

        cfg = load_app_config()
        models_file = load_models_file()
        tiny = None
        for m in models_file.models:
            if getattr(m, "level", None) in ("tiny", "cheap") or "tiny" in m.name.lower():
                tiny = m
                break
        if tiny is None and cfg.default_model:
            tiny = find_model(cfg.default_model)
        if tiny is None:
            return False, "(未找到可用模型，保守判不安全)"
        entry = find_model(tiny.name)
        if entry is None:
            return False, "(模型解析失败，保守判不安全)"

        provider = get_provider(entry)
        resp = provider.chat(
            ChatRequest(model=entry.model, messages=[ChatMessage(role="user", content=prompt)]),
            timeout=15.0,
        )
        content = resp.content or ""
        judge_reason = content.strip() if len(content) <= 200 else content.strip()[:200] + "…"
        is_safe, matched = _parse_safety_verdict(content)
        if not matched:
            return False, f"(判定格式异常，保守判不安全: {judge_reason})"
        return is_safe, judge_reason
    except Exception as e:
        return False, f"(AI 判断失败，保守判不安全: {e})"


def _parse_safety_verdict(content: str) -> tuple[bool, bool]:
    """Extract the judge's verdict from its reply. Returns ``(is_safe, matched)``.

    Only the structured ``结论：安全/不安全`` line counts — the reasoning
    above it naturally restates the unsafe categories ("不涉及支付等不安全
    操作"), so substring checks over the whole reply always saw "不安全" and
    every verdict failed. We take the LAST ``结论`` line; without one we
    fall back to the reply's last non-empty line, and only recognise it when
    it *ends* with the bare verdict word. Anything ambiguous → not matched
    (caller fails closed).
    """
    matches = re.findall(r"结论\s*[：:]\s*(不安全|安全)", content)
    if matches:
        return matches[-1] == "安全", True
    lines = [ln.strip() for ln in content.strip().splitlines() if ln.strip()]
    if lines:
        last = lines[-1].rstrip("。.!！?？ ")
        if last.endswith("不安全"):
            return False, True
        if last.endswith("安全"):
            return True, True
    return False, False
