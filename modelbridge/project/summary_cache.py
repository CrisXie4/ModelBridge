"""On-disk cache for :class:`ProjectSummary`.

Stored at ``<project>/.modelbridge/cache/project_summary.json``. The cache
is invalidated when any of these three content-derived hashes changes:

* ``file_tree_hash``  — the sorted POSIX path listing (cheap walk, no reads)
* ``manifest_hash``   — content of ``package.json`` / ``pyproject.toml`` /
                        ``README.md`` / ... (see :data:`MANIFEST_FILES`)
* ``rules_hash``      — content of ``AGENT.md`` / ``CLAUDE.md`` /
                        ``.cursorrules`` / ``.windsurfrules``

When all three match the cached values we return the deserialised
summary directly, avoiding a re-scan. ``updated_at`` is recorded in the
file but **never read into the prompt** — keeping volatile content out of
the cached prefix is the entire point of this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..utils import now_iso
from .scanner import (
    ProjectSummary,
    compute_file_tree_hash,
    compute_manifest_hash,
    compute_rules_hash,
    scan_project,
)


#: Per-project cache location.
CACHE_DIR_NAME = ".modelbridge"
CACHE_SUBDIR_NAME = "cache"
CACHE_FILE_NAME = "project_summary.json"


def get_summary_cache_path(project_path: Path | str) -> Path:
    root = Path(project_path).expanduser().resolve()
    return root / CACHE_DIR_NAME / CACHE_SUBDIR_NAME / CACHE_FILE_NAME


@dataclass
class CacheCheck:
    """The result of checking whether the cached summary is still valid."""

    valid: bool
    reason: str  # human-readable: "hit" | "no cache" | "file_tree_hash mismatch" | ...
    file_tree_hash: str = ""
    manifest_hash: str = ""
    rules_hash: str = ""
    project_hash: str = ""


def _compute_project_hash(file_tree_hash: str, manifest_hash: str, rules_hash: str) -> str:
    """Combine the three content hashes into a single 16-char fingerprint."""
    blob = f"{file_tree_hash}|{manifest_hash}|{rules_hash}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compute_project_hashes(project_path: Path | str) -> CacheCheck:
    """Compute all three invalidation hashes for ``project_path``.

    No cache read — this is the "current state" snapshot. Use
    :func:`load_cached_summary` to compare against what's on disk.
    """
    fh = compute_file_tree_hash(project_path)
    mh = compute_manifest_hash(project_path)
    rh = compute_rules_hash(project_path)
    ph = _compute_project_hash(fh, mh, rh)
    return CacheCheck(
        valid=False,
        reason="just computed (not yet compared)",
        file_tree_hash=fh,
        manifest_hash=mh,
        rules_hash=rh,
        project_hash=ph,
    )


def load_cached_summary(project_path: Path | str) -> tuple[ProjectSummary | None, CacheCheck]:
    """Try to load the cached summary; report hit / miss reason.

    Returns ``(summary_or_None, check)``. The ``check`` always carries
    the freshly-computed hashes so callers can pass them to
    :func:`save_cached_summary` after a re-scan without recomputing.
    """
    current = compute_project_hashes(project_path)
    cache_path = get_summary_cache_path(project_path)
    if not cache_path.is_file():
        current.reason = "no cache file"
        return None, current

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        current.reason = f"cache unreadable: {e}"
        return None, current

    if not isinstance(data, dict) or "summary" not in data:
        current.reason = "cache malformed"
        return None, current

    cached_fh = str(data.get("file_tree_hash", ""))
    cached_mh = str(data.get("manifest_hash", ""))
    cached_rh = str(data.get("rules_hash", ""))

    if cached_fh != current.file_tree_hash:
        current.reason = f"file_tree_hash changed ({cached_fh} → {current.file_tree_hash})"
        return None, current
    if cached_mh != current.manifest_hash:
        current.reason = f"manifest_hash changed ({cached_mh} → {current.manifest_hash})"
        return None, current
    if cached_rh != current.rules_hash:
        current.reason = f"rules_hash changed ({cached_rh} → {current.rules_hash})"
        return None, current

    try:
        summary = ProjectSummary.from_dict(data["summary"])
    except Exception as e:  # noqa: BLE001 — bad cache should never break callers
        current.reason = f"cache deserialise failed: {e}"
        return None, current

    current.valid = True
    current.reason = "hit"
    return summary, current


def save_cached_summary(
    project_path: Path | str,
    summary: ProjectSummary,
    check: CacheCheck,
) -> Path | None:
    """Persist ``summary`` keyed by the three hashes from ``check``.

    Returns the path written, or ``None`` if writing failed (caller is
    expected to silently fall back — caching never breaks the caller).

    Note: ``updated_at`` is included for human inspection but MUST NOT
    flow into the prompt. ``ProjectSummary.to_markdown()`` already
    excludes it (it's not a field on :class:`ProjectSummary`).
    """
    cache_path = get_summary_cache_path(project_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    payload = {
        "project_path": str(Path(project_path).expanduser().resolve()),
        "project_hash": check.project_hash,
        "file_tree_hash": check.file_tree_hash,
        "manifest_hash": check.manifest_hash,
        "rules_hash": check.rules_hash,
        "summary": summary.to_dict(),
        "updated_at": now_iso(),  # diagnostic only — NEVER read into prompt
    }
    try:
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return None
    return cache_path


def scan_project_cached(
    project_path: Path | str,
    *,
    force_refresh: bool = False,
) -> tuple[ProjectSummary, CacheCheck]:
    """Return a :class:`ProjectSummary`, preferring the on-disk cache.

    Flow:

    1. Compute current ``file_tree_hash`` + ``manifest_hash`` + ``rules_hash``.
    2. If a cache file exists with matching hashes → return its summary.
    3. Otherwise call :func:`scan_project` and refresh the cache.

    ``force_refresh=True`` skips step 2 (still recomputes hashes so the
    fresh cache is keyed correctly).
    """
    if not force_refresh:
        cached, check = load_cached_summary(project_path)
        if cached is not None:
            return cached, check

    fresh = scan_project(project_path)
    check = compute_project_hashes(project_path)
    # ``scan_project`` already filled file_tree_hash on the summary —
    # belt + braces: keep them in sync.
    fresh.file_tree_hash = check.file_tree_hash
    save_cached_summary(project_path, fresh, check)
    check.valid = False
    if check.reason in ("just computed (not yet compared)", "no cache file"):
        check.reason = "refreshed"
    return fresh, check


__all__ = [
    "CacheCheck",
    "compute_project_hashes",
    "get_summary_cache_path",
    "load_cached_summary",
    "save_cached_summary",
    "scan_project_cached",
    "CACHE_DIR_NAME",
    "CACHE_SUBDIR_NAME",
    "CACHE_FILE_NAME",
]
