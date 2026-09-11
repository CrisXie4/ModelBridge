"""Tool-output bounding with spill-to-disk — model sees a preview, disk keeps it all.

Borrowed from opencode (``core/tool-output-store.ts``): when a tool produces
more text than the budget, the *model-visible* result becomes a head+tail
preview plus a pointer, while the full output is spilled to a managed file
under ``~/.modelbridge/tool-output/``. The model can recover the detail on
demand (``read_file`` on the spill path), and nothing is lost — the opposite
of a hard truncation.

* Preview is **line-snapped** (never cuts a line in half) and weighted
  ~70% head / 30% tail — start of output carries the command echo and the
  first errors; end carries the summary / final status.
* Spill files are exclusive-created with an ascending name so concurrent
  calls can't clobber each other.
* A lazy sweep deletes spill files older than :data:`RETENTION_DAYS` — run
  at most once per hour per process (a cheap monotonic-clock gate), so no
  background thread is needed.

Adaptive hint: when the session has a subagent tool registered
(``ctx.delegable``), the truncation marker suggests delegating the file to
the subagent instead of reading it all into the main context — same idea as
opencode's agent-aware truncation hint.
"""

from __future__ import annotations

import itertools
import time
from pathlib import Path

from ...utils import get_app_dir

__all__ = [
    "RETENTION_DAYS",
    "DEFAULT_MAX_CHARS",
    "spill_root",
    "spill_output",
    "sweep_spills",
    "bound_output",
]

#: Spill files older than this are deleted by the lazy sweep.
RETENTION_DAYS = 7
#: Default model-visible budget for command output (chars).
DEFAULT_MAX_CHARS = 8_000
#: Seconds between lazy sweeps (one per process per hour is plenty).
_SWEEP_INTERVAL = 3600.0

_SPILL_DIR_NAME = "tool-output"
_COUNTER = itertools.count(1)
_last_sweep = 0.0


def spill_root() -> Path:
    return get_app_dir() / _SPILL_DIR_NAME


def sweep_spills(*, now: float | None = None, force: bool = False) -> int:
    """Delete spill files older than :data:`RETENTION_DAYS`. Returns count removed.

    Rate-limited to once per :data:`_SWEEP_INTERVAL` unless ``force=True``
    (tests pass ``force`` and inject ``now``).
    """
    global _last_sweep
    clock = time.monotonic() if now is None else now
    if not force and (clock - _last_sweep) < _SWEEP_INTERVAL:
        return 0
    _last_sweep = clock
    removed = 0
    root = spill_root()
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    cutoff = (time.time() if now is None else now) - RETENTION_DAYS * 86400
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def spill_output(tool_name: str, content: str, *, now: float | None = None) -> Path | None:
    """Write ``content`` to a new spill file; returns the path, or ``None`` on failure.

    Never raises — a spill failure must degrade to a plain truncation, not
    crash the tool. ``now`` exists for tests (deterministic filenames).
    """
    sweep_spills(now=now)
    root = spill_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") if now is None else time.strftime(
            "%Y%m%d-%H%M%S", time.localtime(now)
        )
        path = root / f"{tool_name}_{stamp}_{next(_COUNTER):04d}.txt"
        # 'x' = exclusive create: concurrent tool calls can't clobber.
        with path.open("x", encoding="utf-8") as f:
            f.write(content)
        return path
    except OSError:
        return None


def _snap_to_line(text: str, *, keep_end: bool) -> str:
    """Drop a trailing (or leading, ``keep_end``) partial line so the preview
    never ends mid-line."""
    if not text:
        return text
    if keep_end:
        idx = text.find("\n")
        return text[idx + 1 :] if idx != -1 else text
    idx = text.rfind("\n")
    return text[: idx + 1] if idx != -1 else ""


def bound_output(
    content: str,
    *,
    tool: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    delegable: bool = False,
) -> tuple[str, bool]:
    """Return ``(model_visible_text, truncated)`` under a ``max_chars`` budget.

    Within budget the text passes through untouched. Over budget the full
    content is spilled to disk and the visible text becomes a line-snapped
    head+tail preview whose marker carries the spill path — plus a
    ``read_file`` hint, and a delegate hint when the session has a subagent
    tool. If spilling fails we degrade to a plain truncation marker.
    """
    if len(content) <= max_chars:
        return content, False

    path = spill_output(tool, content)

    # ~70% head / 30% tail of the budget, then snap to line boundaries.
    head_budget = int(max_chars * 0.7)
    tail_budget = max_chars - head_budget
    head = _snap_to_line(content[:head_budget], keep_end=False)
    tail = _snap_to_line(content[-tail_budget:], keep_end=True)

    if path is not None:
        rel = path
        hint = f"完整输出已保存到 {rel}，可用 read_file 按需读取片段"
        if delegable:
            hint += "，或把该文件交给 spawn_subagent 子代理汇总，避免占用主上下文"
        marker = f"\n[…输出超过 {max_chars} 字符已截断；{hint}…]\n"
    else:
        marker = f"\n[…输出超过 {max_chars} 字符已截断…]\n"

    middle = len(content) - len(head) - len(tail)
    if middle <= 0:
        # Degenerate split (pathological content) — head alone is honest.
        return head + marker.strip("\n"), True
    return f"{head}{marker}{tail}", True
