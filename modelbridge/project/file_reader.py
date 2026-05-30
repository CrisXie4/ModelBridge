"""Read source files for prompt injection — *with sharp caps*.

We never paste a whole repo into the model. Each file is capped at
:data:`MAX_LINES_PER_FILE` lines / :data:`MAX_BYTES_PER_FILE` bytes.
When a file exceeds either limit we emit:

* the **head** (configurable lines), then
* a **signatures** block listing every ``def`` / ``class`` / ``func`` /
  ``function`` / ``interface`` / ``type`` declaration we found in the
  tail.

This keeps cost predictable while still telling the model what's in
the file. Binary / image / archive files are skipped outright;
sensitive basenames (``.env`` family etc.) double-check against the
scanner's safety list before opening.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .file_selector import SelectedFile
from .scanner import SENSITIVE_FILE_PATTERNS


# --- caps -------------------------------------------------------------------

MAX_LINES_PER_FILE: int = 300
MAX_BYTES_PER_FILE: int = 10 * 1024  # 10 KB
HEAD_LINES_ON_TRUNCATE: int = 80
"""When truncating, how many leading lines to keep verbatim."""

# Binary / large-asset suffixes — skipped entirely.
_BINARY_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".wav", ".ogg", ".webm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".class", ".jar",
    ".db", ".sqlite", ".sqlite3",
})


# Suffix → fenced-code-block language hint for the prompt rendering.
_FENCE_LANG: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "jsx", ".mjs": "js",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".swift": "swift",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".ini": "ini",
    ".md": "markdown", ".rst": "rst",
    ".sql": "sql", ".html": "html", ".css": "css",
    ".lua": "lua", ".scala": "scala",
}


# Per-language declaration regexes for the "signatures" tail.
# Each pattern captures the signature line (no body). We're forgiving:
# false positives are fine, the goal is "tell the model what's inside".
_SIGNATURE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "py": [
        re.compile(r"^\s*(async\s+)?def\s+[\w_]+\s*\(.*?\).*?:"),
        re.compile(r"^\s*class\s+[\w_]+(\s*\(.*\))?\s*:"),
    ],
    "ts": [
        re.compile(r"^\s*(export\s+)?(async\s+)?function\s+[\w$]+\s*\(.*?\)"),
        re.compile(r"^\s*(export\s+)?(default\s+)?class\s+[\w$]+(\s+extends\s+[\w$.]+)?"),
        re.compile(r"^\s*(export\s+)?interface\s+[\w$]+"),
        re.compile(r"^\s*(export\s+)?type\s+[\w$]+\s*="),
        re.compile(r"^\s*(export\s+)?(const|let|var)\s+[\w$]+\s*=\s*\(.*?\)\s*=>"),
    ],
    "go": [
        re.compile(r"^\s*func\s+(\([^)]*\)\s+)?[\w$]+\s*\(.*?\).*$"),
        re.compile(r"^\s*type\s+[\w$]+\s+(struct|interface)\b"),
    ],
    "rust": [
        re.compile(r"^\s*(pub\s+)?(async\s+)?fn\s+[\w_]+\s*[<(].*$"),
        re.compile(r"^\s*(pub\s+)?(struct|enum|trait|impl)\s+[\w_]+"),
    ],
    "java": [
        re.compile(r"^\s*(public|private|protected|static|final|abstract|\s)*\s*(class|interface|enum)\s+[\w$]+"),
        re.compile(r"^\s*(public|private|protected|static|final|abstract|\s)+[\w<>,\[\]\s.$]+\s+[\w$]+\s*\([^)]*\)\s*(throws\s+[\w$.,\s]+)?\s*\{?"),
    ],
    "cs": [
        re.compile(r"^\s*(public|private|protected|internal|static|abstract|sealed|\s)*\s*(class|interface|struct|record)\s+[\w$]+"),
    ],
    "ruby": [
        re.compile(r"^\s*(def|class|module)\s+[\w_:]+"),
    ],
    "php": [
        re.compile(r"^\s*(public\s+|private\s+|protected\s+|static\s+|abstract\s+|final\s+)*function\s+\w+\s*\("),
        re.compile(r"^\s*(abstract\s+|final\s+)?class\s+\w+"),
    ],
    "bash": [
        re.compile(r"^\s*[a-zA-Z_][\w_]*\s*\(\)\s*\{"),
        re.compile(r"^\s*function\s+[\w_]+\s*\("),
    ],
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FileContext:
    """One file ready to splice into a prompt."""

    path: str
    snippet: str
    truncated: bool = False
    lines_read: int = 0
    bytes_read: int = 0
    skipped_reason: str | None = None
    """Set when the file was skipped (binary, sensitive, missing); ``snippet`` is empty."""

    @property
    def is_empty(self) -> bool:
        return not self.snippet and self.skipped_reason is None

    @property
    def chars(self) -> int:
        return len(self.snippet)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_files(
    selected: Iterable[SelectedFile],
    *,
    project_root: Path | str,
    max_lines: int = MAX_LINES_PER_FILE,
    max_bytes: int = MAX_BYTES_PER_FILE,
) -> list[FileContext]:
    """Read each selected file with truncation. Order preserved."""
    root = Path(project_root).expanduser().resolve()
    out: list[FileContext] = []
    for sf in selected:
        out.append(_read_one(root, sf.path, max_lines=max_lines, max_bytes=max_bytes))
    return out


def render_file_context(ctx: FileContext) -> str:
    """Render a single FileContext as a Markdown block.

    Caller (PromptBuilder) joins these into the ``project_files`` section.
    """
    if ctx.skipped_reason:
        return f"# File: {ctx.path}\n\n_skipped: {ctx.skipped_reason}_\n"
    lang = _fence_lang_for(ctx.path)
    trunc_note = ""
    if ctx.truncated:
        trunc_note = f"_truncated to {ctx.lines_read} lines (max {MAX_LINES_PER_FILE}); tail summarised as signatures._\n\n"
    fence_open = f"```{lang}" if lang else "```"
    return (
        f"# File: {ctx.path}\n\n"
        f"{trunc_note}"
        f"{fence_open}\n{ctx.snippet.rstrip()}\n```\n"
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _read_one(
    root: Path,
    rel_path: str,
    *,
    max_lines: int,
    max_bytes: int,
) -> FileContext:
    abs_path = (root / rel_path).resolve()
    # Path traversal guard — resolved path must stay under root.
    try:
        abs_path.relative_to(root)
    except ValueError:
        return FileContext(path=rel_path, snippet="", skipped_reason="escapes project root")

    basename = PurePosixPath(rel_path).name
    if _is_sensitive(basename):
        return FileContext(path=rel_path, snippet="", skipped_reason="sensitive file (refused)")

    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        return FileContext(path=rel_path, snippet="", skipped_reason=f"binary/asset ({suffix})")

    if not abs_path.is_file():
        return FileContext(path=rel_path, snippet="", skipped_reason="not a regular file")

    try:
        size = abs_path.stat().st_size
    except OSError as e:
        return FileContext(path=rel_path, snippet="", skipped_reason=f"stat failed: {e}")

    if size == 0:
        return FileContext(path=rel_path, snippet="", lines_read=0, bytes_read=0)

    # Stream — read up to max_bytes * 4 raw (so we can decode); we'll
    # cap on lines in the snippet stage.
    try:
        raw = abs_path.read_bytes()[: max(max_bytes * 4, max_bytes + 4096)]
    except OSError as e:
        return FileContext(path=rel_path, snippet="", skipped_reason=f"read failed: {e}")

    # Binary sniff: if the first 2 KB has too many NULs, treat as binary.
    head_sniff = raw[:2048]
    if head_sniff.count(b"\x00") > 4:
        return FileContext(path=rel_path, snippet="", skipped_reason="binary content")

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return FileContext(path=rel_path, snippet="", skipped_reason="decode failed")

    lines = text.splitlines()
    total_lines = len(lines)
    truncated = False
    snippet_lines: list[str]

    if total_lines <= max_lines and len(text.encode("utf-8")) <= max_bytes:
        snippet_lines = lines
    else:
        truncated = True
        head = lines[:HEAD_LINES_ON_TRUNCATE]
        tail = lines[HEAD_LINES_ON_TRUNCATE:]
        signatures = _extract_signatures(tail, suffix=suffix)
        snippet_lines = list(head)
        snippet_lines.append("")
        snippet_lines.append(
            f"# … (skipped {len(tail)} lines; showing signatures only)"
        )
        if signatures:
            snippet_lines.extend(signatures)
        else:
            snippet_lines.append("# (no further declarations detected)")

    # Final byte-cap pass on the joined snippet.
    snippet = "\n".join(snippet_lines)
    encoded = snippet.encode("utf-8")
    if len(encoded) > max_bytes:
        truncated = True
        # Cut to max_bytes, then trim to last newline to avoid mid-line mess.
        cut = encoded[:max_bytes].decode("utf-8", errors="ignore")
        last_nl = cut.rfind("\n")
        if last_nl > max_bytes // 2:
            cut = cut[:last_nl]
        snippet = cut + "\n# … (byte-cap reached)"

    return FileContext(
        path=rel_path,
        snippet=snippet,
        truncated=truncated,
        lines_read=min(total_lines, len(snippet_lines)),
        bytes_read=min(size, len(snippet.encode("utf-8"))),
    )


def _is_sensitive(basename: str) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(basename, pat) for pat in SENSITIVE_FILE_PATTERNS)


def _fence_lang_for(rel_path: str) -> str:
    return _FENCE_LANG.get(PurePosixPath(rel_path).suffix.lower(), "")


def _signature_lang(suffix: str) -> str | None:
    """Map a file suffix to the key used in :data:`_SIGNATURE_PATTERNS`."""
    if suffix in (".py", ".pyi"):
        return "py"
    if suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
        return "ts"
    if suffix == ".go":
        return "go"
    if suffix == ".rs":
        return "rust"
    if suffix in (".java", ".kt"):
        return "java"
    if suffix == ".cs":
        return "cs"
    if suffix == ".rb":
        return "ruby"
    if suffix == ".php":
        return "php"
    if suffix in (".sh", ".bash", ".zsh"):
        return "bash"
    return None


def _extract_signatures(lines: list[str], *, suffix: str) -> list[str]:
    lang = _signature_lang(suffix)
    if lang is None:
        return []
    patterns = _SIGNATURE_PATTERNS.get(lang, [])
    if not patterns:
        return []
    hits: list[str] = []
    for raw in lines:
        # Don't try to be clever — first match wins.
        line = raw.rstrip()
        for pat in patterns:
            if pat.match(line):
                # Truncate very long signatures.
                hits.append(line if len(line) <= 200 else line[:200] + " …")
                break
        if len(hits) >= 60:  # keep the tail list manageable
            hits.append("# … (more declarations elided)")
            break
    return hits


__all__ = [
    "MAX_LINES_PER_FILE",
    "MAX_BYTES_PER_FILE",
    "HEAD_LINES_ON_TRUNCATE",
    "FileContext",
    "read_files",
    "render_file_context",
]
