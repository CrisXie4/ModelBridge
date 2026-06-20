"""Apply a :class:`ParsedDiff` to files on disk.

Strategy
--------

For each :class:`FileDiff` we walk the target file's lines and apply
the hunks **in order**, using the hunk's removed-line block as the
anchor:

1. Build the "before" sequence — every context + removed line.
2. Search for that sequence in the file starting at
   ``hunk.old_start - 1`` (1-based → 0-based), then widen the search
   to a small window (``±FUZZ_LINES``) if the exact line doesn't match.
3. If found, splice in the "after" sequence (context + added lines).
4. If not found, the whole file's apply attempt fails — we never
   write a half-applied file. The other files in the diff may still
   apply or fail independently.

This is intentionally fuzzy enough to tolerate models that get the
hunk line numbers slightly wrong but the context lines right. We
refuse to fall back to *purely* pattern-based application when even
the context doesn't match — better to fail loudly than to splice in
the wrong place.

New files (``--- /dev/null``) and deletions (``+++ /dev/null``) are
supported. Deletion requires that the file content match the hunks'
"removed" block; we don't ``rm`` arbitrary paths.

The applier never touches a file the safety guard refused — the CLI
caller is expected to have already filtered the diff through
:func:`modelbridge.editor.safety.guard_path`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .diff_parser import FileDiff, Hunk, ParsedDiff


# How many lines either side of the hunk's claimed start to search.
FUZZ_LINES: int = 5


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------

@dataclass
class FileApplyResult:
    """Outcome for one :class:`FileDiff`."""

    path: str
    status: str          # "ok" / "skipped" / "failed"
    operation: str       # "modify" / "create" / "delete"
    reason: str = ""     # populated on failure / skip
    hunks_applied: int = 0
    hunks_total: int = 0
    original_text: str | None = None  # what the file looked like before (for backup)


@dataclass
class ApplyResult:
    """Outcome of :func:`apply_diff`."""

    files: list[FileApplyResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def all_ok(self) -> bool:
        return all(f.status == "ok" for f in self.files) and bool(self.files)

    @property
    def any_failure(self) -> bool:
        return any(f.status == "failed" for f in self.files)

    @property
    def modified(self) -> list[str]:
        return [f.path for f in self.files if f.status == "ok" and f.operation == "modify"]

    @property
    def created(self) -> list[str]:
        return [f.path for f in self.files if f.status == "ok" and f.operation == "create"]

    @property
    def deleted(self) -> list[str]:
        return [f.path for f in self.files if f.status == "ok" and f.operation == "delete"]

    @property
    def failures(self) -> list[FileApplyResult]:
        return [f for f in self.files if f.status == "failed"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_diff(
    parsed: ParsedDiff,
    *,
    project_root: Path | str,
    dry_run: bool = False,
) -> ApplyResult:
    """Apply every :class:`FileDiff` in ``parsed`` to disk.

    ``dry_run=True`` performs all the matching work but never writes,
    so callers can preview the outcome.
    """
    root = Path(project_root).expanduser().resolve()
    out = ApplyResult(dry_run=dry_run)

    for fd in parsed.files:
        out.files.append(_apply_one(fd, root=root, dry_run=dry_run))

    return out


# ---------------------------------------------------------------------------
# Per-file apply
# ---------------------------------------------------------------------------

def _apply_one(fd: FileDiff, *, root: Path, dry_run: bool) -> FileApplyResult:
    target_rel = fd.effective_path

    if fd.is_creation:
        return _apply_create(fd, root=root, dry_run=dry_run, target_rel=target_rel)
    if fd.is_deletion:
        return _apply_delete(fd, root=root, dry_run=dry_run, target_rel=target_rel)

    return _apply_modify(fd, root=root, dry_run=dry_run, target_rel=target_rel)


def _apply_create(
    fd: FileDiff, *, root: Path, dry_run: bool, target_rel: str,
) -> FileApplyResult:
    abs_path = root / target_rel
    if abs_path.exists():
        return FileApplyResult(
            path=target_rel, status="failed", operation="create",
            reason=f"目标文件已存在: {target_rel}",
            hunks_total=len(fd.hunks),
        )
    if not fd.hunks:
        return FileApplyResult(
            path=target_rel, status="failed", operation="create",
            reason="新建文件 diff 缺少 hunk",
        )
    # New file: there should be exactly one hunk with only '+' (or context, but
    # context isn't meaningful for a /dev/null source).
    new_lines: list[str] = []
    for h in fd.hunks:
        for ln in h.lines:
            if ln.is_remove:
                return FileApplyResult(
                    path=target_rel, status="failed", operation="create",
                    reason="新建文件 diff 不应包含 '-' 行",
                    hunks_total=len(fd.hunks),
                )
            new_lines.append(ln.text)
    if not dry_run:
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except OSError as e:
            return FileApplyResult(
                path=target_rel, status="failed", operation="create",
                reason=f"写入失败: {e}", hunks_total=len(fd.hunks),
            )
    return FileApplyResult(
        path=target_rel, status="ok", operation="create",
        hunks_applied=len(fd.hunks), hunks_total=len(fd.hunks),
        original_text=None,  # didn't exist
    )


def _apply_delete(
    fd: FileDiff, *, root: Path, dry_run: bool, target_rel: str,
) -> FileApplyResult:
    abs_path = root / target_rel
    if not abs_path.is_file():
        return FileApplyResult(
            path=target_rel, status="failed", operation="delete",
            reason=f"目标文件不存在，无法删除: {target_rel}",
            hunks_total=len(fd.hunks),
        )
    try:
        original = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return FileApplyResult(
            path=target_rel, status="failed", operation="delete",
            reason=f"读取原文件失败: {e}",
            hunks_total=len(fd.hunks),
        )
    # Verify the "removed" content matches the file (defensive).
    removed: list[str] = []
    for h in fd.hunks:
        for ln in h.lines:
            if ln.is_add:
                return FileApplyResult(
                    path=target_rel, status="failed", operation="delete",
                    reason="删除文件 diff 不应包含 '+' 行",
                    hunks_total=len(fd.hunks),
                )
            removed.append(ln.text)
    # An empty ``removed`` set (a /dev/null deletion whose hunk carries no
    # context/removed lines) means we have nothing to verify against — treat
    # that as "cannot verify → refuse", not as "verification passed".
    # Otherwise a model could unlink a file whose content it never matched.
    if not removed or "\n".join(removed).rstrip() != original.rstrip("\n"):
        # Not fatal but we surface a warning by failing — the user can
        # delete manually if they really meant to.
        return FileApplyResult(
            path=target_rel, status="failed", operation="delete",
            reason="待删内容缺失或与文件实际内容不一致，拒绝删除",
            hunks_total=len(fd.hunks), original_text=original,
        )
    if not dry_run:
        try:
            abs_path.unlink()
        except OSError as e:
            return FileApplyResult(
                path=target_rel, status="failed", operation="delete",
                reason=f"unlink 失败: {e}", hunks_total=len(fd.hunks),
                original_text=original,
            )
    return FileApplyResult(
        path=target_rel, status="ok", operation="delete",
        hunks_applied=len(fd.hunks), hunks_total=len(fd.hunks),
        original_text=original,
    )


def _apply_modify(
    fd: FileDiff, *, root: Path, dry_run: bool, target_rel: str,
) -> FileApplyResult:
    abs_path = root / target_rel
    if not abs_path.is_file():
        return FileApplyResult(
            path=target_rel, status="failed", operation="modify",
            reason=f"目标文件不存在: {target_rel}", hunks_total=len(fd.hunks),
        )
    try:
        original = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return FileApplyResult(
            path=target_rel, status="failed", operation="modify",
            reason=f"读取原文件失败: {e}", hunks_total=len(fd.hunks),
        )

    # Preserve trailing newline behaviour.
    had_trailing_nl = original.endswith("\n")
    lines: list[str] = original.split("\n")
    if had_trailing_nl and lines and lines[-1] == "":
        lines.pop()

    new_lines = list(lines)
    # We apply hunks in order. After each hunk the offset between
    # 'old line numbers in the diff' and 'current line numbers in
    # new_lines' can drift; track it.
    offset = 0
    hunks_applied = 0

    for h in fd.hunks:
        before = _hunk_before(h)
        after = _hunk_after(h)
        anchor = h.old_start - 1 + offset  # 0-based

        idx = _find_anchor(new_lines, before, around=anchor, fuzz=FUZZ_LINES)
        if idx is None:
            return FileApplyResult(
                path=target_rel, status="failed", operation="modify",
                reason=(
                    f"hunk @@ -{h.old_start} 在文件中找不到匹配的上下文。"
                    "通常意味着文件已被改动或 diff 行号与现实不符。"
                ),
                hunks_applied=hunks_applied, hunks_total=len(fd.hunks),
                original_text=original,
            )
        new_lines[idx:idx + len(before)] = after
        offset += len(after) - len(before)
        hunks_applied += 1

    # Reconstruct text
    new_text = "\n".join(new_lines)
    if had_trailing_nl:
        new_text += "\n"

    if not dry_run:
        try:
            abs_path.write_text(new_text, encoding="utf-8")
        except OSError as e:
            return FileApplyResult(
                path=target_rel, status="failed", operation="modify",
                reason=f"写入失败: {e}", hunks_applied=hunks_applied,
                hunks_total=len(fd.hunks), original_text=original,
            )
    return FileApplyResult(
        path=target_rel, status="ok", operation="modify",
        hunks_applied=hunks_applied, hunks_total=len(fd.hunks),
        original_text=original,
    )


# ---------------------------------------------------------------------------
# Hunk-side helpers
# ---------------------------------------------------------------------------

def _hunk_before(h: Hunk) -> list[str]:
    """Lines the hunk expects in the original file (context + removed)."""
    return [ln.text for ln in h.lines if not ln.is_add]


def _hunk_after(h: Hunk) -> list[str]:
    """Lines the hunk wants in the new file (context + added)."""
    return [ln.text for ln in h.lines if not ln.is_remove]


def _find_anchor(
    haystack: list[str], needle: list[str], *, around: int, fuzz: int,
) -> int | None:
    """Find ``needle`` in ``haystack`` starting near index ``around``.

    Tries ``around`` first, then walks outward (closest first) up to
    ``fuzz`` lines. Returns the start index or ``None`` if not found.
    """
    if not needle:
        # Pure-insert hunk (no context, no removed lines) — anchor exactly
        # at the claimed position, clamped to bounds.
        return max(0, min(around, len(haystack)))

    n = len(needle)

    def matches(start: int) -> bool:
        if start < 0 or start + n > len(haystack):
            return False
        return haystack[start:start + n] == needle

    # First try the exact claimed offset.
    candidates: list[int] = [around]
    for d in range(1, fuzz + 1):
        candidates.append(around - d)
        candidates.append(around + d)
    for c in candidates:
        if matches(c):
            return c

    # As a last resort, scan the whole file once. If the diff line
    # number is wildly off but context is unique, this still wins.
    for i in range(0, len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return i
    return None


__all__ = [
    "FUZZ_LINES",
    "FileApplyResult",
    "ApplyResult",
    "apply_diff",
]
