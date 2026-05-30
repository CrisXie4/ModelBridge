"""Context-budget planner — keep total prompt chars under a hard cap.

Why this lives here
-------------------
PromptBuilder happily concatenates whatever it's handed. The phase-5
``chat --project`` flow may select 8 files × ~1 KB each plus a project
summary plus rules — easily 15 KB before the model sees a single
token of the user's question. This module is the one place that
decides "we're over budget; drop the least-important section first".

The default cap (:data:`DEFAULT_MAX_CONTEXT_CHARS`) is conservative on
purpose — fitting comfortably inside even a 4K-token Chinese-heavy
context window. Override with :func:`plan` ``max_chars`` per-call.

Priority order (highest → lowest, kept-first when trimming)
-----------------------------------------------------------

1. **user_query** — never trimmed
2. **core_system / global_rules / project_rules** — trimmed together as
   one block, only as a last resort
3. **project_summary** — kept (it's small, ~1-2 KB)
4. **README + entrypoint files** — the always-included anchors
5. **other selected files** — dropped or signature-only first
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..project.file_reader import FileContext, render_file_context


DEFAULT_MAX_CONTEXT_CHARS: int = 20_000
"""Default cap; lifts to whatever the caller passes."""

#: Files that are always considered "anchors" — README, manifests, entrypoints.
_ANCHOR_BASENAMES: frozenset[str] = frozenset({
    "README.md", "README.rst", "README.txt", "README",
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml",
    "main.py", "app.py", "manage.py", "server.py",
    "index.js", "index.ts", "server.js", "server.ts",
    "main.go", "main.rs",
})


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ContextPlan:
    """Outcome of :func:`plan`."""

    kept_files: list[FileContext] = field(default_factory=list)
    """Files (possibly with shortened ``snippet``) that fit the budget."""
    dropped_files: list[str] = field(default_factory=list)
    """Paths of files removed entirely to stay under budget."""
    truncated_files: list[str] = field(default_factory=list)
    """Paths of files we shortened (head-only) to stay under budget."""
    fits_files_chars: int = 0
    """How many chars the kept files block will contribute."""
    overhead_chars: int = 0
    """Chars reserved for rules + system + summary + user_query."""
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def plan(
    files: list[FileContext],
    *,
    user_query: str = "",
    rules_chars: int = 0,
    system_chars: int = 0,
    project_summary_chars: int = 0,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> ContextPlan:
    """Trim ``files`` until everything fits in ``max_chars``.

    The caller is responsible for re-running PromptBuilder with the
    ``kept_files`` from this plan.
    """
    max_chars = max(2_000, int(max_chars))
    user_chars = len(user_query or "")
    overhead = user_chars + rules_chars + system_chars + project_summary_chars
    # Reserve some headroom for separators / markdown fences.
    overhead += 256

    files_budget = max(0, max_chars - overhead)
    out = ContextPlan(
        max_chars=max_chars,
        overhead_chars=overhead,
    )

    if not files:
        return out

    if overhead >= max_chars:
        out.warnings.append(
            f"rules+system+summary 已经超出 max_chars ({overhead} ≥ {max_chars}), "
            "无法放入任何项目文件。"
        )
        out.dropped_files = [f.path for f in files]
        return out

    # Score files so we know what to drop last.
    scored: list[tuple[int, FileContext]] = []
    for f in files:
        if f.skipped_reason or not f.snippet:
            # Carry skipped files through — they're free (no body).
            scored.append((10_000, f))
            continue
        anchor = _basename(f.path) in _ANCHOR_BASENAMES
        # Higher score = keep longer. Anchors > short files > big files.
        s = (1_000 if anchor else 0) + max(0, 5_000 - f.chars)
        scored.append((s, f))
    scored.sort(key=lambda t: -t[0])

    used = 0
    kept: list[FileContext] = []
    dropped: list[str] = []
    truncated: list[str] = []

    for _score, f in scored:
        if f.skipped_reason:
            kept.append(f)
            used += len(render_file_context(f))
            continue

        rendered = render_file_context(f)
        cost = len(rendered)
        if used + cost <= files_budget:
            kept.append(f)
            used += cost
            continue

        # Try shrinking by keeping only the head (first ~30 lines).
        shrunk = _shrink_to_head(f)
        cost_shrunk = len(render_file_context(shrunk))
        if used + cost_shrunk <= files_budget:
            kept.append(shrunk)
            used += cost_shrunk
            truncated.append(f.path)
            out.warnings.append(
                f"{f.path}: 因 context 预算 ({max_chars}) 进一步截断为头部+签名。"
            )
        else:
            dropped.append(f.path)
            out.warnings.append(
                f"{f.path}: 因 context 预算 ({max_chars}) 整体丢弃。"
            )

    if dropped:
        out.warnings.insert(0, "context truncated to fit model limits")

    # Restore original ordering for the kept files.
    order_index = {f.path: i for i, f in enumerate(files)}
    kept.sort(key=lambda f: order_index.get(f.path, 0))

    out.kept_files = kept
    out.dropped_files = dropped
    out.truncated_files = truncated
    out.fits_files_chars = used
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basename(path: str) -> str:
    return PurePosixPath(path).name


_SIGNATURE_LINE = re.compile(
    r"^\s*("
    r"def\s+|class\s+|"
    r"function\s+|interface\s+|type\s+\w+\s*=|"
    r"func\s+|fn\s+|"
    r"public\s+|private\s+|protected\s+|"
    r"export\s+(default\s+)?(class|function|const|let|var|interface|type|async)\s+"
    r")"
)


def _shrink_to_head(f: FileContext, *, keep_lines: int = 30) -> FileContext:
    """Return a new FileContext with only the first ``keep_lines`` lines + signatures."""
    if not f.snippet:
        return f
    lines = f.snippet.splitlines()
    head = lines[:keep_lines]
    sigs: list[str] = []
    for line in lines[keep_lines:]:
        if _SIGNATURE_LINE.match(line):
            sigs.append(line.rstrip() if len(line) <= 200 else line[:200] + " …")
        if len(sigs) >= 30:
            sigs.append("# … (more declarations elided)")
            break
    new_lines = head + [""]
    if sigs:
        new_lines.append(f"# … ({len(lines) - keep_lines} lines elided; signatures only)")
        new_lines.extend(sigs)
    else:
        new_lines.append(f"# … ({len(lines) - keep_lines} lines elided)")
    return FileContext(
        path=f.path,
        snippet="\n".join(new_lines),
        truncated=True,
        lines_read=len(new_lines),
        bytes_read=f.bytes_read,
    )


__all__ = [
    "DEFAULT_MAX_CONTEXT_CHARS",
    "ContextPlan",
    "plan",
]
