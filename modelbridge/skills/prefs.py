"""Per-skill enable/disable state — persisted at ``~/.modelbridge/skills.json``.

Skills stay on disk; this store only decides whether :func:`wire_skills`
injects them into the system prompt. Format is deliberately minimal::

    {"disabled": ["old-workflow", "experimental-x"]}

Absent name = enabled. Corrupt file = everything enabled (never break the
REPL over a preference).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..utils import get_app_dir

SKILLS_FILE_NAME = "skills.json"


def prefs_path() -> Path:
    return get_app_dir() / SKILLS_FILE_NAME


def load_disabled(path: Path | None = None) -> set[str]:
    """Disabled skill names. Missing/corrupt file → empty set."""
    target = path or prefs_path()
    try:
        raw = json.loads(target.read_text("utf-8"))
    except Exception:
        return set()
    items = raw.get("disabled") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return set()
    return {str(x) for x in items}


def save_disabled(disabled: Iterable[str], path: Path | None = None) -> None:
    """Persist the disabled list (sorted for stable diffs)."""
    target = path or prefs_path()
    payload = {"disabled": sorted(set(disabled))}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")


def set_enabled(name: str, enabled: bool, *, path: Path | None = None) -> None:
    """Enable/disable one skill by name and persist immediately."""
    disabled = load_disabled(path)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    save_disabled(disabled, path)


__all__ = [
    "prefs_path",
    "load_disabled",
    "save_disabled",
    "set_enabled",
]
