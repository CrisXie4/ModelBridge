"""Phase-6: diff-driven file editing.

Public surface:

* :func:`build_edit_messages` / :func:`extract_diff` — prompt + response handling.
* :func:`parse_unified_diff` / :class:`ParsedDiff` — diff parser.
* :func:`apply_diff` / :class:`ApplyResult` — patch applier.
* :func:`create_backup` / :func:`rollback` / :func:`list_backups` — backup + rollback.
* :func:`guard_path` — path safety check.
"""

from .backup import (
    BACKUPS_DIRNAME,
    BackupRecord,
    RollbackResult,
    create_backup,
    latest_backup,
    list_backups,
    mark_deletions,
    rollback,
)
from .diff_parser import (
    DiffParseError,
    FileDiff,
    Hunk,
    HunkLine,
    ParsedDiff,
    parse_unified_diff,
    render_unified_diff,
)
from .edit_prompt import (
    EDIT_SYSTEM_RULES,
    EditPromptResult,
    ExtractedDiff,
    build_edit_messages,
    extract_diff,
)
from .patch_applier import (
    FUZZ_LINES,
    ApplyResult,
    FileApplyResult,
    apply_diff,
)
from .safety import (
    SafetyVerdict,
    guard_path,
    guard_paths,
    guard_project_root,
    is_sensitive_basename,
    strip_ab_prefix,
)

__all__ = [
    # safety
    "SafetyVerdict",
    "guard_path",
    "guard_paths",
    "is_sensitive_basename",
    "strip_ab_prefix",
    # diff parser
    "DiffParseError",
    "HunkLine",
    "Hunk",
    "FileDiff",
    "ParsedDiff",
    "parse_unified_diff",
    "render_unified_diff",
    # applier
    "FUZZ_LINES",
    "FileApplyResult",
    "ApplyResult",
    "apply_diff",
    # backup
    "BACKUPS_DIRNAME",
    "BackupRecord",
    "RollbackResult",
    "create_backup",
    "mark_deletions",
    "list_backups",
    "latest_backup",
    "rollback",
    # edit prompt
    "EDIT_SYSTEM_RULES",
    "EditPromptResult",
    "ExtractedDiff",
    "build_edit_messages",
    "extract_diff",
]
