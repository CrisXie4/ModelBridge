"""Path safety guard for diff-driven file edits.

Anything the patch applier touches must survive :func:`guard_path` first.
The check is paranoid on purpose — a malicious / hallucinating model
should not be able to overwrite ``.env``, escape via ``..``, or stamp
on ``.git/``.

Sources of truth
----------------

We deliberately **share** the user's REPL-side security config so a single
edit to ``~/.modelbridge/config.yaml`` covers both modes:

* ``security.allowed_project_dirs`` — :func:`guard_project_root` rejects
  ``--project`` paths that fall outside this allowlist. Same rule the
  agent REPL's :class:`PathPolicy` uses.
* ``security.block_sensitive_files`` — appended to the built-in
  :data:`SENSITIVE_FILE_PATTERNS` from ``scanner.py``. So if a user adds
  ``config.json`` there to block reads in the REPL, ``mbridge edit``
  will refuse to patch ``config.json`` too.

The directory blocklist (``.git`` / ``node_modules`` / …) stays
hard-coded here — it's about *what AI is allowed to suggest*, not about
the user's filesystem layout.

Rules
-----

1. Path must be **relative** (no absolute paths, no Windows drive letters).
2. No segment may be ``..``.
3. No segment may be a known forbidden directory.
4. No segment (and especially the basename) may match a sensitive-file
   pattern (built-in + user-configured).
5. After resolving with ``project_root``, the resulting path must stay
   inside ``project_root``.
6. Symlinks must not escape ``project_root``.

The check is purely structural; it never reads file content.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Iterable

from ..config import load_app_config
from ..project.scanner import SENSITIVE_FILE_PATTERNS


# Directories the patch applier MUST NEVER write into.
#
# Note: deliberately narrower than scanner's ``SKIP_DIRS``. Scanner skips
# ``bin/`` / ``out/`` because they're *usually* build output; the editor
# does NOT block them because they're *also* legitimate source dirs for
# CLI tools (``package.json`` ``"bin"`` field, Cargo binaries, Python
# entry-point scripts). Blocking edits there would prevent ``mbridge
# edit`` from working on the very class of projects ModelBridge targets.
#
# Keep the list to things that are *never* user source:
#   - VCS metadata
#   - secret stores
#   - dependency snapshots
#   - virtualenvs
#   - tool caches
#   - unambiguous build-only outputs (``dist`` / framework-specific)
#   - IDE state
_FORBIDDEN_DIRS: frozenset[str] = frozenset({
    # VCS
    ".git", ".hg", ".svn",
    # Secret stores
    ".ssh", ".gnupg",
    # Vendored / installed dependencies
    "node_modules", "bower_components", "vendor", "Pods",
    # Virtualenvs
    ".venv", "venv",
    # Python / tool caches
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".cache", ".turbo",
    # Framework-specific build outputs (these are *always* generated)
    "dist", ".next", ".nuxt", ".output",
    # IDE state
    ".idea", ".vscode",
})

# NOT in _FORBIDDEN_DIRS — kept here so future readers see the trade-off:
#   bin/      — npm "bin" field source, Cargo `src/bin/`, Python CLI scripts.
#   build/    — sometimes a generated dir, sometimes a CMake source dir.
#   out/      — Next.js `next export` output, but also some build-tool sources.
#   obj/      — .NET build output; rare outside .NET, leave to user judgement.
#   target/   — Cargo build output, but Java/Maven also use it for source-ish.
#   env/      — usually a venv, but occasionally a config dir; allow with care.
# If you find yourself bitten by an AI patching a `dist/foo.js`, prefer
# tightening the prompt or scoping ``--project`` to a child dir over
# growing this list — false-blocks here are silent failures users hate.


@dataclass
class SafetyVerdict:
    """Result of one :func:`guard_path` call."""

    ok: bool
    reason: str = ""
    path: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _user_block_patterns() -> tuple[str, ...]:
    """Return the user-configured sensitive-file patterns from config.yaml.

    Failure to load config (corrupt YAML, missing field) returns an empty
    tuple — defence-in-depth: the built-in :data:`SENSITIVE_FILE_PATTERNS`
    still applies, the user-extension just doesn't.
    """
    try:
        cfg = load_app_config()
        return tuple(cfg.security.block_sensitive_files or [])
    except Exception:
        return ()


def _all_sensitive_patterns() -> tuple[str, ...]:
    return tuple(SENSITIVE_FILE_PATTERNS) + _user_block_patterns()


def is_sensitive_basename(basename: str) -> bool:
    """True if the basename matches any sensitive-file glob (built-in + user)."""
    return any(fnmatch(basename, pat) for pat in _all_sensitive_patterns())


def _matches_user_pattern_anywhere(path_parts: Iterable[str]) -> str | None:
    """Mirror :class:`PathPolicy._check_blocked` — user patterns match any
    component, not just the basename. Returns the matching pattern or None.
    """
    user_patterns = _user_block_patterns()
    for pat in user_patterns:
        for part in path_parts:
            if fnmatch(part, pat):
                return pat
    return None


def guard_path(rel_path: str, project_root: Path | str) -> SafetyVerdict:
    """Validate that ``rel_path`` may be edited inside ``project_root``."""
    if not rel_path or not isinstance(rel_path, str):
        return SafetyVerdict(False, "空路径", path=str(rel_path))

    # 1) reject absolute paths / drive letters
    p = PurePosixPath(rel_path)
    if p.is_absolute() or rel_path.startswith(("/", "\\")):
        return SafetyVerdict(False, f"绝对路径不允许: {rel_path!r}", path=rel_path)
    if len(rel_path) >= 2 and rel_path[1] == ":":
        return SafetyVerdict(False, f"Windows 驱动器路径不允许: {rel_path!r}", path=rel_path)

    # 2) reject `..` segments
    parts = p.parts
    if any(seg == ".." for seg in parts):
        return SafetyVerdict(False, f"不允许 '..' 跳目录: {rel_path!r}", path=rel_path)

    # 3) reject forbidden directory segments anywhere in the path
    for seg in parts[:-1]:
        if seg in _FORBIDDEN_DIRS:
            return SafetyVerdict(
                False, f"不允许写入受保护目录 {seg!r}: {rel_path!r}", path=rel_path,
            )

    # 4a) basename sensitivity check (built-in + user)
    basename = p.name
    if is_sensitive_basename(basename):
        return SafetyVerdict(
            False, f"敏感文件名拒绝写入: {basename}", path=rel_path,
        )
    # 4b) user-configured patterns also apply to any path component
    hit = _matches_user_pattern_anywhere(parts)
    if hit is not None:
        return SafetyVerdict(
            False,
            f"路径命中 config.security.block_sensitive_files 规则 {hit!r}: {rel_path}",
            path=rel_path,
        )

    # 5) resolve against project_root and ensure containment
    root = Path(project_root).expanduser().resolve()
    try:
        resolved = (root / rel_path).resolve()
    except (OSError, RuntimeError) as e:
        return SafetyVerdict(False, f"路径解析失败: {e}", path=rel_path)
    try:
        resolved.relative_to(root)
    except ValueError:
        return SafetyVerdict(
            False, f"路径逃逸出项目根: {rel_path!r} → {resolved}", path=rel_path,
        )

    # 6) symlink escape
    if resolved.is_symlink():
        try:
            target = resolved.resolve(strict=False)
            target.relative_to(root)
        except (OSError, ValueError):
            return SafetyVerdict(
                False, f"符号链接逃逸出项目根: {rel_path!r}", path=rel_path,
            )

    return SafetyVerdict(True, "", path=rel_path)


def strip_ab_prefix(path: str) -> str:
    """Strip the optional ``a/`` / ``b/`` prefix git-style diffs use.

    Also strips a single leading ``./``. Returns the path unchanged if
    no prefix is present, or if the path is ``/dev/null`` (sentinel for
    create / delete).
    """
    if not path:
        return path
    if path == "/dev/null":
        return path
    for pre in ("a/", "b/"):
        if path.startswith(pre):
            return path[len(pre):]
    if path.startswith("./"):
        return path[2:]
    return path


def guard_paths(paths: Iterable[str], project_root: Path | str) -> list[SafetyVerdict]:
    """Convenience: run :func:`guard_path` over many paths.

    ``/dev/null`` is allowed through (the caller decides whether
    create/delete is permitted for that side of the diff).
    """
    out: list[SafetyVerdict] = []
    for p in paths:
        if p == "/dev/null":
            out.append(SafetyVerdict(True, "/dev/null sentinel", path=p))
            continue
        out.append(guard_path(p, project_root))
    return out


# ---------------------------------------------------------------------------
# Project-root allowlist — shared with agent REPL's PathPolicy.
# ---------------------------------------------------------------------------

def guard_project_root(project_root: Path | str) -> SafetyVerdict:
    """Reject ``--project`` paths that fall outside the user's allowlist.

    Reads ``config.yaml: security.allowed_project_dirs`` (same field the
    REPL's :class:`PathPolicy` honours). An empty list = no allowlist =
    any project allowed (the historical default; the REPL's
    "未配置 allowed_project_dirs" guard only kicks in *with* a `--cwd`
    contradiction, so this matches its semantics).
    """
    try:
        cfg = load_app_config()
        allowed = list(cfg.security.allowed_project_dirs or [])
    except Exception:
        allowed = []

    if not allowed:
        return SafetyVerdict(True, "no allowlist configured", path=str(project_root))

    root = Path(project_root).expanduser().resolve()
    for entry in allowed:
        try:
            allowed_root = Path(entry).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        try:
            root.relative_to(allowed_root)
            return SafetyVerdict(True, "", path=str(root))
        except ValueError:
            continue

    pretty = ", ".join(str(Path(e).expanduser()) for e in allowed)
    return SafetyVerdict(
        False,
        f"项目目录 {root} 不在 config.security.allowed_project_dirs 之内 "
        f"(允许: {pretty})。如要放行，请编辑 ~/.modelbridge/config.yaml。",
        path=str(root),
    )


__all__ = [
    "SafetyVerdict",
    "guard_path",
    "guard_paths",
    "guard_project_root",
    "strip_ab_prefix",
    "is_sensitive_basename",
]
