"""In-memory file index for ``@file`` mentions in the chat REPL.

Walks a project root once (reusing the scanner's skip-dir and
sensitive-file rules) and keeps a flat, sorted list of relative POSIX
paths — **both files and directories**, because the user wants folders
to show up in the ``@`` dropdown too.

``match(query)`` does a cheap case-insensitive *substring* filter:
typing ``@1234`` surfaces every path that contains ``1234``. Results are
ranked so that basename hits beat path-only hits, earlier hits beat
later ones, and shorter paths win ties — i.e. the file you almost
certainly meant floats to the top.

Deliberately NOT a semantic index. It's a list scan; for a 5k-file repo
that's sub-millisecond, and we cap the index at :data:`MAX_ENTRIES`
anyway so a monorepo can't make the REPL janky.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from .scanner import (
    MODELBRIDGE_INTERNAL_SUBDIRS,
    SENSITIVE_FILE_PATTERNS,
    SKIP_DIRS,
)


#: Hard cap on indexed entries so a monorepo can't make matching janky.
MAX_ENTRIES: int = 5000

#: Default number of suggestions returned by :meth:`FileIndex.match`.
DEFAULT_LIMIT: int = 20


@dataclass(frozen=True)
class FileEntry:
    """One indexed path — a file or a directory."""

    relpath: str
    """POSIX-relative path under the project root (no trailing slash)."""
    is_dir: bool = False


@dataclass
class FileIndex:
    """A flat, sorted index of a project's files and directories."""

    root: Path
    entries: list[FileEntry] = field(default_factory=list)
    truncated: bool = False
    """True if the project exceeded :data:`MAX_ENTRIES` and was capped."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, root: Path | str, *, max_entries: int = MAX_ENTRIES) -> "FileIndex":
        """Walk ``root`` and collect non-skipped files + directories."""
        root_path = Path(root).expanduser().resolve()
        entries: list[FileEntry] = []
        truncated = False

        if not root_path.is_dir():
            return cls(root=root_path, entries=[], truncated=False)

        # Collect everything (bounded by a generous safety cap so a giant repo
        # can't OOM), THEN sort and truncate — so the survivors are the
        # lexicographically-first max_entries deterministically, not whatever
        # os.walk reached first.
        hard_cap = max(max_entries * 10, max_entries)

        for dirpath, dirnames, filenames in os.walk(root_path):
            here = Path(dirpath)
            rel_here = _relposix(here, root_path)

            # Prune directories we never descend into. Mutating ``dirnames``
            # in place is what makes os.walk skip them.
            pruned: list[str] = []
            for d in dirnames:
                if d in SKIP_DIRS:
                    continue
                # Mirror the scanner's dot-dir policy: skip hidden dirs so
                # credential / CI homes (.aws, .ssh, .config, .github …) never
                # enter the @ index. Keep .modelbridge — it's the tool's own
                # config the user may legitimately want to mention.
                if d.startswith(".") and d != ".modelbridge":
                    continue
                # Inside .modelbridge/, drop tooling-state subdirs so the
                # cache/logs/sessions never pollute the @ menu.
                if rel_here == ".modelbridge" and d in MODELBRIDGE_INTERNAL_SUBDIRS:
                    continue
                pruned.append(d)
            dirnames[:] = pruned

            # Record the surviving directories as entries (skip the root).
            for d in dirnames:
                rel = _relposix(here / d, root_path)
                if rel:
                    entries.append(FileEntry(relpath=rel, is_dir=True))

            for fn in filenames:
                if _is_sensitive(fn):
                    continue
                rel = _relposix(here / fn, root_path)
                entries.append(FileEntry(relpath=rel, is_dir=False))

            if len(entries) >= hard_cap:
                truncated = True
                break

        entries.sort(key=lambda e: e.relpath)
        if len(entries) > max_entries:
            truncated = True
            entries = entries[:max_entries]
        return cls(root=root_path, entries=entries, truncated=truncated)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def match(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[FileEntry]:
        """Return up to ``limit`` entries whose path contains ``query``.

        Empty query → the first ``limit`` entries (already path-sorted).
        Otherwise a case-insensitive substring filter, ranked so the most
        likely-intended path comes first.
        """
        limit = max(1, int(limit))
        # Normalize Windows backslashes so '@modelbridge\agent' matches the
        # POSIX relpaths the index stores (the live completer feeds us the raw
        # partial; submit-time resolution normalizes separately).
        q = query.strip().replace("\\", "/").lower()
        if not q:
            return self.entries[:limit]

        # A slashed query means the user is drilling into a path; allow the
        # match to skip intervening directory segments (subsequence). A bare
        # query keeps strict '含有' substring semantics so short queries
        # stay clean.
        use_subseq = "/" in q

        scored: list[tuple[tuple, FileEntry]] = []
        for e in self.entries:
            rel_lower = e.relpath.lower()
            if use_subseq:
                if not _is_subsequence(q, rel_lower):
                    continue
                # Reward entries whose basename contains the last segment of
                # the query (the part after the final '/') — that's the file
                # the user is actually narrowing toward.
                seg = q.rsplit("/", 1)[-1]
                base_hit = bool(seg) and seg in _basename(rel_lower)
                key = (0 if base_hit else 1, 0, len(e.relpath), e.relpath)
            else:
                pos = rel_lower.find(q)
                if pos < 0:
                    continue
                base = _basename(rel_lower)
                base_pos = base.find(q)
                in_base = base_pos >= 0
                # Sort key (ascending): basename hits first, earlier hits
                # first, shorter paths first, then lexicographic for stability.
                key = (
                    0 if in_base else 1,
                    base_pos if in_base else pos,
                    len(e.relpath),
                    e.relpath,
                )
            scored.append((key, e))

        scored.sort(key=lambda kv: kv[0])
        return [e for _, e in scored[:limit]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _relposix(p: Path, root: Path) -> str:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return ""
    s = rel.as_posix()
    return "" if s == "." else s


def _basename(rel_lower: str) -> str:
    # str.rsplit is ~15x faster than PurePosixPath(...).name and this runs
    # per-entry on every keystroke; relpaths never carry a trailing slash.
    return rel_lower.rsplit("/", 1)[-1]


def _is_subsequence(needle: str, hay: str) -> bool:
    """True if every char of ``needle`` appears in ``hay`` in order."""
    it = iter(hay)
    return all(ch in it for ch in needle)


def _is_sensitive(basename: str) -> bool:
    return any(fnmatch(basename, pat) for pat in SENSITIVE_FILE_PATTERNS)


__all__ = ["FileEntry", "FileIndex", "MAX_ENTRIES", "DEFAULT_LIMIT"]
