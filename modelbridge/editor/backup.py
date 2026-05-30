"""Backup + rollback for diff-driven edits.

Layout
------

Backups live in ``<project>/.modelbridge/backups/`` so they're tied to
the project, not the user's global state. Each backup is one directory:

    .modelbridge/backups/
    ├── 2026-05-23_143012_edit/
    │   ├── meta.json
    │   ├── patch.diff
    │   └── files/
    │       └── src/
    │           └── auth.py        ← original content
    └── 2026-05-23_142107_edit/
        ├── ...

A ``meta.json`` records the user request, the timestamp, the list of
files modified / created / deleted, and the path to the patch. Files
that were *created* by the patch have a marker file
``files/<path>.created`` instead of original content — rollback then
deletes the created file rather than restoring nothing.

Rollback
--------

``rollback()`` picks the most recent non-rolled-back backup directory,
restores each file (or deletes created ones), then renames the
directory to ``<ts>_edit.rolledback`` so it isn't picked up again.
History is preserved on disk, just shifted out of the active stack.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


BACKUPS_DIRNAME = ".modelbridge/backups"
ROLLED_BACK_SUFFIX = ".rolledback"
CREATED_MARKER = ".created"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BackupRecord:
    """One backup on disk."""

    dir: Path
    timestamp: str
    user_request: str
    modified: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    patch_path: str = ""

    def to_meta_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "user_request": self.user_request,
            "modified": self.modified,
            "created": self.created,
            "deleted": self.deleted,
            "patch": self.patch_path,
        }

    @classmethod
    def from_meta_dict(cls, dir: Path, data: dict[str, Any]) -> "BackupRecord":
        return cls(
            dir=dir,
            timestamp=str(data.get("timestamp", "")),
            user_request=str(data.get("user_request", "")),
            modified=list(data.get("modified", []) or []),
            created=list(data.get("created", []) or []),
            deleted=list(data.get("deleted", []) or []),
            patch_path=str(data.get("patch", "")),
        )


@dataclass
class RollbackResult:
    backup: BackupRecord | None = None
    restored: list[str] = field(default_factory=list)
    re_deleted: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    """``[(path, reason), ...]`` for files that couldn't be restored."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _backups_dir(project_root: Path) -> Path:
    return project_root / BACKUPS_DIRNAME


def _sanitize_label(label: str) -> str:
    """Make ``label`` filesystem-safe (used in directory name)."""
    label = re.sub(r"[^A-Za-z0-9_\-]+", "_", label or "edit")[:40]
    return label or "edit"


# ---------------------------------------------------------------------------
# Public API — create backup
# ---------------------------------------------------------------------------

def create_backup(
    project_root: Path | str,
    *,
    user_request: str,
    patch_text: str,
    files_to_save: dict[str, str | None],
    label: str = "edit",
) -> BackupRecord:
    """Snapshot ``files_to_save`` before a patch is applied.

    ``files_to_save`` maps **project-relative POSIX path → original
    content** (or ``None`` if the file is being *created* — we still
    record the marker so rollback knows to delete it).
    """
    root = Path(project_root).expanduser().resolve()
    backups_root = _backups_dir(root)
    backups_root.mkdir(parents=True, exist_ok=True)

    ts = _ts()
    backup_dir = backups_root / f"{ts}_{_sanitize_label(label)}"
    # Avoid collisions (sub-second creates).
    suffix = 1
    while backup_dir.exists():
        backup_dir = backups_root / f"{ts}_{_sanitize_label(label)}_{suffix}"
        suffix += 1
    backup_dir.mkdir(parents=True)

    files_dir = backup_dir / "files"
    files_dir.mkdir()

    modified: list[str] = []
    created: list[str] = []
    deleted: list[str] = []

    for rel_path, original in files_to_save.items():
        target = files_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if original is None:
            # Marker: this file is being *created* by the patch.
            (target.parent / (target.name + CREATED_MARKER)).write_text(
                "created-by-patch\n", encoding="utf-8",
            )
            created.append(rel_path)
            continue
        # An "original" value of "" means the file existed but was empty.
        target.write_text(original, encoding="utf-8")
        modified.append(rel_path)

    patch_path = backup_dir / "patch.diff"
    patch_path.write_text(patch_text or "", encoding="utf-8")

    record = BackupRecord(
        dir=backup_dir,
        timestamp=ts,
        user_request=user_request,
        modified=modified,
        created=created,
        deleted=deleted,
        patch_path="patch.diff",
    )
    (backup_dir / "meta.json").write_text(
        json.dumps(record.to_meta_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def mark_deletions(record: BackupRecord, deleted: list[str], originals: dict[str, str]) -> None:
    """After the applier ran, record which files it deleted.

    The original content for each deletion must already be in
    ``originals``; we save it into the backup's ``files/`` mirror so
    rollback can restore it.
    """
    if not deleted:
        return
    files_dir = record.dir / "files"
    for rel_path in deleted:
        original = originals.get(rel_path, "")
        target = files_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original, encoding="utf-8")
        record.deleted.append(rel_path)
    (record.dir / "meta.json").write_text(
        json.dumps(record.to_meta_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public API — list / inspect
# ---------------------------------------------------------------------------

def list_backups(project_root: Path | str) -> list[BackupRecord]:
    """Return active (non-rolled-back) backups, newest first."""
    root = Path(project_root).expanduser().resolve()
    backups_root = _backups_dir(root)
    if not backups_root.is_dir():
        return []
    out: list[BackupRecord] = []
    for d in sorted(backups_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        if d.name.endswith(ROLLED_BACK_SUFFIX):
            continue
        meta_path = d / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(BackupRecord.from_meta_dict(d, data))
    return out


def latest_backup(project_root: Path | str) -> BackupRecord | None:
    backups = list_backups(project_root)
    return backups[0] if backups else None


# ---------------------------------------------------------------------------
# Public API — rollback
# ---------------------------------------------------------------------------

def rollback(project_root: Path | str) -> RollbackResult:
    """Undo the most recent active backup. Returns details of what happened."""
    root = Path(project_root).expanduser().resolve()
    record = latest_backup(root)
    if record is None:
        return RollbackResult()

    out = RollbackResult(backup=record)
    files_dir = record.dir / "files"

    # Restore modified files.
    for rel_path in record.modified:
        src = files_dir / rel_path
        dst = root / rel_path
        if not src.is_file():
            out.failures.append((rel_path, "备份中找不到原始文件"))
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            content = src.read_text(encoding="utf-8")
            dst.write_text(content, encoding="utf-8")
            out.restored.append(rel_path)
        except OSError as e:
            out.failures.append((rel_path, f"恢复失败: {e}"))

    # For files the patch CREATED, rollback = delete them again.
    for rel_path in record.created:
        dst = root / rel_path
        try:
            if dst.is_file():
                dst.unlink()
                out.re_deleted.append(rel_path)
            else:
                # Already gone, treat as success.
                out.re_deleted.append(rel_path)
        except OSError as e:
            out.failures.append((rel_path, f"删除失败: {e}"))

    # For files the patch DELETED, rollback = recreate from backup.
    for rel_path in record.deleted:
        src = files_dir / rel_path
        dst = root / rel_path
        if not src.is_file():
            out.failures.append((rel_path, "备份中找不到被删文件内容"))
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            out.restored.append(rel_path)
        except OSError as e:
            out.failures.append((rel_path, f"恢复失败: {e}"))

    # Mark this backup as consumed.
    if not out.failures:
        try:
            record.dir.rename(record.dir.with_name(record.dir.name + ROLLED_BACK_SUFFIX))
        except OSError:
            # Couldn't rename — not fatal, but the next rollback would
            # re-undo the same backup. Write a sentinel instead.
            try:
                (record.dir / ".rolledback").write_text(_ts(), encoding="utf-8")
            except OSError:
                pass

    return out


__all__ = [
    "BACKUPS_DIRNAME",
    "BackupRecord",
    "RollbackResult",
    "create_backup",
    "mark_deletions",
    "list_backups",
    "latest_backup",
    "rollback",
]
