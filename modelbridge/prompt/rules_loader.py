"""Discover + merge rule files (AGENT.md / CLAUDE.md / .cursorrules / etc.).

Priority (highest first, written to merged output first so it "wins"
when the model reads top-down):

1. Project root          — ``AGENT.md`` / ``AGENTS.md`` / ``CLAUDE.md``
                           / ``.cursorrules`` / ``.windsurfrules``
2. Project .modelbridge/ — ``rules.md`` / ``prompt.md``
3. User global           — ``~/.modelbridge/system.md`` (system_file)
                           ``~/.modelbridge/rules.md`` (user_rules_file)

System prompt (``~/.modelbridge/system.md``) is loaded separately by the
builder; this module only handles **rules** content. ``load_global_rules``
returns rules.md only; the system prompt has its own loader.

Reads are best-effort: a file that fails to decode or is too large gets
logged in :class:`MergedRules.warnings` but never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..config import load_app_config
from ..utils import get_app_dir


# Project-level rule file names, ordered by preference.
PROJECT_RULE_FILES: tuple[str, ...] = (
    "AGENT.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".windsurfrules",
)

# Inside .modelbridge/ under the project (also under the user global dir).
NESTED_RULE_FILES: tuple[str, ...] = (
    "rules.md",
    "prompt.md",
)

NESTED_DIR_NAME = ".modelbridge"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RuleFile:
    """A single rule file we found on disk."""

    path: Path
    scope: str  # "project" | "project_nested" | "user_global"
    label: str  # used as the heading when merged (e.g. "AGENT.md")
    size: int = 0


@dataclass
class MergedRules:
    """Output of :func:`merge_rules` — text + provenance + warnings."""

    text: str = ""
    files: list[RuleFile] = field(default_factory=list)
    total_chars: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_rule_files(project_path: Path | str | None) -> list[RuleFile]:
    """Walk the search locations and return rule files actually on disk.

    Order: project root → project ``.modelbridge/`` → user global.
    Missing locations are silently skipped — callers can detect the
    "nothing found" case by checking ``len(...) == 0``.
    """
    found: list[RuleFile] = []

    if project_path is not None:
        proj = Path(project_path).expanduser().resolve()
        if proj.exists() and proj.is_dir():
            for name in PROJECT_RULE_FILES:
                p = proj / name
                if p.is_file():
                    found.append(RuleFile(path=p, scope="project", label=name, size=_safe_size(p)))
            nested = proj / NESTED_DIR_NAME
            if nested.is_dir():
                for name in NESTED_RULE_FILES:
                    p = nested / name
                    if p.is_file():
                        found.append(RuleFile(
                            path=p, scope="project_nested",
                            label=f"{NESTED_DIR_NAME}/{name}", size=_safe_size(p),
                        ))

    # User-global rules.md (system.md is handled separately).
    cfg = load_app_config()
    user_rules_path = _resolve_user_path(
        cfg.prompt.user_rules_file if hasattr(cfg, "prompt") else None,
        default=get_app_dir() / "rules.md",
    )
    if user_rules_path.is_file():
        found.append(RuleFile(
            path=user_rules_path, scope="user_global",
            label=str(user_rules_path.name), size=_safe_size(user_rules_path),
        ))

    return found


def load_system_prompt() -> str | None:
    """Return the user's system prompt text (``~/.modelbridge/system.md``).

    Returns ``None`` if no system file is configured or the file is
    missing / unreadable.
    """
    cfg = load_app_config()
    pcfg = getattr(cfg, "prompt", None)
    system_path = _resolve_user_path(
        getattr(pcfg, "system_file", None) if pcfg else None,
        default=get_app_dir() / "system.md",
    )
    if not system_path.is_file():
        return None
    try:
        return system_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_global_rules() -> MergedRules:
    """Just ``~/.modelbridge/rules.md`` (no project scan)."""
    cfg = load_app_config()
    pcfg = getattr(cfg, "prompt", None)
    user_rules_path = _resolve_user_path(
        getattr(pcfg, "user_rules_file", None) if pcfg else None,
        default=get_app_dir() / "rules.md",
    )
    if not user_rules_path.is_file():
        return MergedRules()
    files = [RuleFile(
        path=user_rules_path, scope="user_global",
        label=str(user_rules_path.name), size=_safe_size(user_rules_path),
    )]
    return merge_rules(files)


def load_project_rules(project_path: Path | str) -> MergedRules:
    """All rule files for ``project_path`` (no global rules)."""
    files = [f for f in discover_rule_files(project_path) if f.scope != "user_global"]
    return merge_rules(files)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_rules(
    files: Iterable[RuleFile],
    *,
    max_chars: int | None = None,
) -> MergedRules:
    """Concatenate ``files`` into a single Markdown string with provenance.

    The merged output looks like::

        # Rules from AGENT.md (project)

        <contents>

        # Rules from CLAUDE.md (project)

        <contents>

    Honours ``config.prompt.max_rules_chars`` (override via ``max_chars``).
    On overflow we truncate the *concatenated* output and set
    ``truncated=True``. Earlier files (higher priority) thus keep more
    bytes than later ones.
    """
    if max_chars is None:
        try:
            cfg = load_app_config()
            max_chars = int(getattr(cfg.prompt, "max_rules_chars", 20000) or 20000)
        except Exception:  # noqa: BLE001 — config errors don't break loading
            max_chars = 20000

    blocks: list[str] = []
    seen_files: list[RuleFile] = []
    warnings: list[str] = []
    chars_used = 0

    for f in files:
        try:
            text = f.path.read_text(encoding="utf-8", errors="replace").rstrip()
        except OSError as e:
            warnings.append(f"读取失败 {f.path}: {e}")
            continue
        if not text:
            continue

        scope_tag = _scope_human(f.scope)
        heading = f"# Rules from {f.label} ({scope_tag})"
        block = f"{heading}\n\n{text}\n"

        # If adding this block overflows, take a slice that fits and stop.
        remaining = max_chars - chars_used
        if remaining <= 0:
            warnings.append(f"已达到 max_rules_chars={max_chars}，跳过 {f.label}")
            break
        if len(block) > remaining:
            # Keep the header so provenance survives even when truncated.
            slice_ = block[:remaining].rstrip()
            slice_ += "\n\n[... truncated at max_rules_chars ...]\n"
            blocks.append(slice_)
            chars_used += len(slice_)
            seen_files.append(f)
            return MergedRules(
                text="\n".join(blocks).rstrip() + "\n",
                files=seen_files,
                total_chars=chars_used,
                truncated=True,
                warnings=warnings,
            )

        blocks.append(block)
        chars_used += len(block)
        seen_files.append(f)

    return MergedRules(
        text="\n".join(blocks).rstrip() + ("\n" if blocks else ""),
        files=seen_files,
        total_chars=chars_used,
        truncated=False,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_user_path(configured: str | None, *, default: Path) -> Path:
    if not configured:
        return default
    p = Path(configured).expanduser()
    return p


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _scope_human(scope: str) -> str:
    return {
        "project": "project",
        "project_nested": "project/.modelbridge",
        "user_global": "user global",
    }.get(scope, scope)


__all__ = [
    "PROJECT_RULE_FILES",
    "NESTED_RULE_FILES",
    "RuleFile",
    "MergedRules",
    "discover_rule_files",
    "load_global_rules",
    "load_project_rules",
    "load_system_prompt",
    "merge_rules",
]
