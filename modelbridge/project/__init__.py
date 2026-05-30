"""Project scanning + AGENT.md generation + file selection / reading.

* :class:`ProjectSummary` / :func:`scan_project` — read-only scanner.
* :func:`select_files` / :class:`SelectedFile` — keyword-driven file picker.
* :func:`read_files` / :class:`FileContext` — capped file reader for prompt injection.
* :func:`generate_agent_md` / :func:`write_agent_md` — produce AGENT.md.
"""

from .file_reader import (
    HEAD_LINES_ON_TRUNCATE,
    MAX_BYTES_PER_FILE,
    MAX_LINES_PER_FILE,
    FileContext,
    read_files,
    render_file_context,
)
from .file_selector import (
    DEFAULT_TOP_N,
    HARD_CAP,
    MIN_SCORE_KEEP,
    SelectedFile,
    SelectionResult,
    select_files,
)
from .init_md import (
    DEFAULT_TARGET_FILENAME,
    GenerationResult,
    build_prompt,
    generate_agent_md,
    write_agent_md,
)
from .scanner import (
    LANG_SUFFIXES,
    MANIFEST_FILES,
    RULE_FILES_FOR_HASH,
    SENSITIVE_FILE_PATTERNS,
    SKIP_DIRS,
    ProjectSummary,
    compute_file_tree_hash,
    compute_manifest_hash,
    compute_rules_hash,
    scan_project,
)
from .summary_cache import (
    CACHE_DIR_NAME,
    CACHE_FILE_NAME,
    CACHE_SUBDIR_NAME,
    CacheCheck,
    compute_project_hashes,
    get_summary_cache_path,
    load_cached_summary,
    save_cached_summary,
    scan_project_cached,
)

__all__ = [
    "ProjectSummary",
    "scan_project",
    "scan_project_cached",
    "compute_file_tree_hash",
    "compute_manifest_hash",
    "compute_rules_hash",
    "compute_project_hashes",
    "load_cached_summary",
    "save_cached_summary",
    "get_summary_cache_path",
    "CacheCheck",
    "MANIFEST_FILES",
    "RULE_FILES_FOR_HASH",
    "SENSITIVE_FILE_PATTERNS",
    "SKIP_DIRS",
    "LANG_SUFFIXES",
    "CACHE_DIR_NAME",
    "CACHE_SUBDIR_NAME",
    "CACHE_FILE_NAME",
    "SelectedFile",
    "SelectionResult",
    "select_files",
    "DEFAULT_TOP_N",
    "HARD_CAP",
    "MIN_SCORE_KEEP",
    "FileContext",
    "read_files",
    "render_file_context",
    "MAX_LINES_PER_FILE",
    "MAX_BYTES_PER_FILE",
    "HEAD_LINES_ON_TRUNCATE",
    "DEFAULT_TARGET_FILENAME",
    "GenerationResult",
    "build_prompt",
    "generate_agent_md",
    "write_agent_md",
]
