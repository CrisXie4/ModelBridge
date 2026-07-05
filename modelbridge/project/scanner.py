"""Project scanner — builds a :class:`ProjectSummary` for AGENT.md generation.

What it reads
-------------
* Common manifest files (``package.json``, ``pyproject.toml``,
  ``requirements.txt``, ``Cargo.toml``, ``go.mod``, ``pom.xml``,
  ``Dockerfile``, ``docker-compose.yml``).
* ``README.md`` / ``README.rst`` / ``README`` (head excerpt only).
* A trimmed file tree.

What it deliberately does NOT read
----------------------------------
Sensitive files (``.env*``, ``id_rsa``, ``id_ed25519``, ``*.pem``,
``*.key``, anything inside ``.ssh``), big directories (``node_modules``,
``.git``, ``dist``, ``build``, ``__pycache__``, ``.venv``, ``vendor``)
and any file ≥ 200 KB. Sensitive files are recorded in ``notes`` as
"existed but skipped" so the AI can still mention them ("there is a
.env, you should not read it") without ever seeing the contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# --- safety lists -----------------------------------------------------------

#: Files we refuse to open. ``fnmatch`` patterns matched against basename.
SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "*.pem",
    "*.key",
    "*_secret*",
    "*credentials*",
    "secrets.yaml",
    "secrets.yml",
    # Common credential / auth files — text, so the binary-skip won't catch
    # them; without these @file (and the scanner) would read tokens verbatim.
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".pgpass",
    ".htpasswd",
    ".dockercfg",
    "kubeconfig",
    "*.kubeconfig",
    "id_dsa",
    "id_dsa.*",
    "id_ecdsa",
    "id_ecdsa.*",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.ppk",
    "*.ovpn",
    "*.gpg",
    "secring.*",
)

#: Directories we never descend into (matched against directory basename).
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "target", "out", "bin", "obj",
    ".next", ".nuxt", ".output", ".turbo", ".cache",
    "vendor", "Pods",
    ".idea", ".vscode",
    ".ssh",
})

#: Subdirectories of ``.modelbridge/`` that hold tooling state — these
#: must NOT enter the file tree or the cache would invalidate itself on
#: every run (the on-disk ProjectSummary cache lives in ``cache/`` and
#: its presence would otherwise shift ``file_tree_hash``).
MODELBRIDGE_INTERNAL_SUBDIRS: frozenset[str] = frozenset({
    "cache",
    "logs",
    "sessions",
    ".backups",
})

#: Suffixes we count toward language detection.
LANG_SUFFIXES: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".c": "C", ".h": "C/C++",
    ".scala": "Scala",
    ".sh": "Shell",
    ".lua": "Lua",
}

#: Max bytes we'll read out of any single source file (defensive).
MAX_FILE_BYTES = 200 * 1024            # 200 KB
#: Max chars in the README excerpt we paste into the summary.
MAX_README_CHARS = 2_500
#: Max entries in the file_tree section.
MAX_TREE_ENTRIES = 250


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ProjectSummary:
    project_path: Path
    project_name: str
    detected_languages: list[str] = field(default_factory=list)
    detected_frameworks: list[str] = field(default_factory=list)
    package_manager: str | None = None
    entrypoints: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    important_files: list[str] = field(default_factory=list)
    file_tree: list[str] = field(default_factory=list)
    readme_excerpt: str = ""
    notes: list[str] = field(default_factory=list)
    # Phase 5 additions — used by file_selector / file_reader.
    files: list[str] = field(default_factory=list)
    """All non-sensitive, non-skipped files (POSIX-relative paths, sorted)."""
    file_types: dict[str, int] = field(default_factory=dict)
    """Language → file count (e.g. ``{"Python": 12, "TypeScript": 4}``)."""
    ignored_files: list[str] = field(default_factory=list)
    """Sensitive / skipped files we know about but refuse to read."""
    total_files: int = 0
    """Total non-sensitive files discovered (may exceed ``len(files)`` if tree was capped)."""
    # Stable hash of the full (uncapped) sorted POSIX path list. Cheap to
    # recompute and used to invalidate the on-disk summary cache.
    file_tree_hash: str = ""

    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render as a Markdown block suitable for ``project_summary``.

        IMPORTANT: this output is fed into the **stable prompt prefix**. It
        must NOT include the absolute path, the user's home directory, a
        timestamp, or anything else that varies across machines or runs —
        such content would invalidate every provider prefix-cache hit.
        Use ``project_name`` (a portable label) instead of ``project_path``.
        """
        parts: list[str] = []
        parts.append(f"## {self.project_name}")
        # No path line — absolute paths break cache stability cross-machine.
        if self.detected_languages:
            parts.append(f"_languages_: {', '.join(self.detected_languages)}")
        if self.detected_frameworks:
            parts.append(f"_frameworks_: {', '.join(self.detected_frameworks)}")
        if self.package_manager:
            parts.append(f"_package manager_: {self.package_manager}")
        if self.entrypoints:
            parts.append("_entrypoints_: " + ", ".join(f"`{e}`" for e in self.entrypoints))
        if self.scripts:
            parts.append("### scripts")
            for k, v in list(self.scripts.items())[:20]:
                parts.append(f"- `{k}`: {v}")
        if self.important_files:
            parts.append("### important files")
            for f in self.important_files:
                parts.append(f"- `{f}`")
        if self.readme_excerpt:
            parts.append("### README (excerpt)")
            parts.append(self.readme_excerpt.strip())
        if self.file_tree:
            parts.append("### file tree (trimmed)")
            parts.append("```\n" + "\n".join(self.file_tree) + "\n```")
        if self.notes:
            parts.append("### notes")
            for n in self.notes:
                parts.append(f"- {n}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # JSON round-trip (used by summary_cache).
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict. Drops nothing — round-trip exact."""
        return {
            "project_path": str(self.project_path),
            "project_name": self.project_name,
            "detected_languages": list(self.detected_languages),
            "detected_frameworks": list(self.detected_frameworks),
            "package_manager": self.package_manager,
            "entrypoints": list(self.entrypoints),
            "scripts": dict(self.scripts),
            "important_files": list(self.important_files),
            "file_tree": list(self.file_tree),
            "readme_excerpt": self.readme_excerpt,
            "notes": list(self.notes),
            "files": list(self.files),
            "file_types": dict(self.file_types),
            "ignored_files": list(self.ignored_files),
            "total_files": self.total_files,
            "file_tree_hash": self.file_tree_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProjectSummary":
        return cls(
            project_path=Path(d.get("project_path", "")),
            project_name=str(d.get("project_name", "")),
            detected_languages=list(d.get("detected_languages", [])),
            detected_frameworks=list(d.get("detected_frameworks", [])),
            package_manager=d.get("package_manager"),
            entrypoints=list(d.get("entrypoints", [])),
            scripts=dict(d.get("scripts", {})),
            important_files=list(d.get("important_files", [])),
            file_tree=list(d.get("file_tree", [])),
            readme_excerpt=str(d.get("readme_excerpt", "")),
            notes=list(d.get("notes", [])),
            files=list(d.get("files", [])),
            file_types=dict(d.get("file_types", {})),
            ignored_files=list(d.get("ignored_files", [])),
            total_files=int(d.get("total_files", 0) or 0),
            file_tree_hash=str(d.get("file_tree_hash", "")),
        )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_project(path: Path | str) -> ProjectSummary:
    """Walk ``path`` and produce a :class:`ProjectSummary`.

    Never raises on missing files; bad paths return a near-empty summary
    with a note. Caller decides what to do with the result.
    """
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return ProjectSummary(
            project_path=root,
            project_name=root.name or "<unknown>",
            notes=[f"project path 不存在或不是目录: {root}"],
        )

    summary = ProjectSummary(project_path=root, project_name=root.name)
    important: list[str] = []
    notes: list[str] = []
    lang_counts: dict[str, int] = {}
    all_files: list[str] = []
    ignored: list[str] = []

    # --- file tree pass (and language counting in one walk) -------------
    summary.file_tree = _build_file_tree(
        root,
        lang_counts=lang_counts,
        notes=notes,
        all_files=all_files,
        ignored=ignored,
    )
    summary.detected_languages = [
        name for name, _ in sorted(lang_counts.items(), key=lambda x: -x[1])
    ]
    summary.files = all_files
    summary.file_types = dict(lang_counts)
    summary.ignored_files = sorted(set(ignored))
    summary.total_files = len(all_files)
    summary.file_tree_hash = _hash_paths(all_files)

    # --- manifest detection ---------------------------------------------
    if (root / "package.json").is_file():
        _read_package_json(root / "package.json", summary, important)
    if (root / "pyproject.toml").is_file():
        _read_pyproject(root / "pyproject.toml", summary, important)
    if (root / "requirements.txt").is_file():
        important.append("requirements.txt")
        if not summary.package_manager:
            summary.package_manager = "pip"
    if (root / "Cargo.toml").is_file():
        _read_cargo(root / "Cargo.toml", summary, important)
    if (root / "go.mod").is_file():
        _read_go_mod(root / "go.mod", summary, important)
    if (root / "pom.xml").is_file():
        important.append("pom.xml")
        summary.package_manager = summary.package_manager or "maven"
    if (root / "Dockerfile").is_file():
        important.append("Dockerfile")
        summary.detected_frameworks.append("Docker")
    if (root / "docker-compose.yml").is_file() or (root / "docker-compose.yaml").is_file():
        important.append("docker-compose.yml")
    if (root / "Makefile").is_file():
        important.append("Makefile")

    # --- README excerpt --------------------------------------------------
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.is_file():
            summary.readme_excerpt = _read_text_capped(p, MAX_README_CHARS) or ""
            important.append(name)
            break

    # --- common entrypoints ---------------------------------------------
    for name in (
        "main.py", "app.py", "manage.py", "server.py",
        "index.js", "index.ts", "server.js", "server.ts",
        "main.go", "main.rs", "src/main.rs",
        "Main.java", "Program.cs",
    ):
        if (root / name).is_file():
            summary.entrypoints.append(name)

    summary.important_files = sorted(set(important))
    summary.notes.extend(notes)
    summary.detected_frameworks = list(dict.fromkeys(summary.detected_frameworks))
    return summary


# ---------------------------------------------------------------------------
# Manifest readers
# ---------------------------------------------------------------------------

def _read_package_json(p: Path, s: ProjectSummary, important: list[str]) -> None:
    important.append("package.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        s.notes.append(f"package.json 解析失败: {e}")
        return
    s.project_name = data.get("name") or s.project_name
    s.package_manager = s.package_manager or _detect_node_pkg_manager(p.parent)
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    frameworks = []
    if "next" in deps:
        frameworks.append("Next.js")
    if "react" in deps:
        frameworks.append("React")
    if "vue" in deps:
        frameworks.append("Vue")
    if "@nuxt/kit" in deps or "nuxt" in deps:
        frameworks.append("Nuxt")
    if "svelte" in deps or "@sveltejs/kit" in deps:
        frameworks.append("Svelte")
    if "express" in deps:
        frameworks.append("Express")
    if "fastify" in deps:
        frameworks.append("Fastify")
    if "vite" in deps:
        frameworks.append("Vite")
    if "typescript" in deps:
        s.detected_languages = list(dict.fromkeys(["TypeScript"] + s.detected_languages))
    s.detected_frameworks.extend(frameworks)
    scripts = data.get("scripts") or {}
    if isinstance(scripts, dict):
        s.scripts.update({str(k): str(v) for k, v in scripts.items()})


def _detect_node_pkg_manager(d: Path) -> str:
    if (d / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (d / "yarn.lock").is_file():
        return "yarn"
    if (d / "bun.lockb").is_file():
        return "bun"
    return "npm"


def _read_pyproject(p: Path, s: ProjectSummary, important: list[str]) -> None:
    important.append("pyproject.toml")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        s.notes.append(f"pyproject.toml 读取失败: {e}")
        return
    # Avoid bringing in a TOML dep — regex-extract the most useful bits.
    m = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if m:
        s.project_name = m.group(1)
    if "[tool.poetry" in text:
        s.package_manager = s.package_manager or "poetry"
    elif "[tool.pdm" in text:
        s.package_manager = s.package_manager or "pdm"
    elif "[tool.hatch" in text:
        s.package_manager = s.package_manager or "hatch"
    elif "[project]" in text:
        s.package_manager = s.package_manager or "pip / pep621"
    # Frameworks by import-ish strings.
    for needle, fw in (
        ("fastapi", "FastAPI"),
        ("flask", "Flask"),
        ("django", "Django"),
        ("typer", "Typer"),
        ("rich", "rich"),
        ("pytest", "pytest"),
        ("torch", "PyTorch"),
        ("transformers", "Transformers"),
    ):
        if needle in text.lower():
            s.detected_frameworks.append(fw)


def _read_cargo(p: Path, s: ProjectSummary, important: list[str]) -> None:
    important.append("Cargo.toml")
    s.package_manager = s.package_manager or "cargo"
    s.detected_frameworks.append("Cargo")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    m = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if m:
        s.project_name = m.group(1)


def _read_go_mod(p: Path, s: ProjectSummary, important: list[str]) -> None:
    important.append("go.mod")
    s.package_manager = s.package_manager or "go mod"
    s.detected_frameworks.append("Go modules")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    m = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    if m:
        s.project_name = m.group(1).split("/")[-1]


# ---------------------------------------------------------------------------
# File tree
# ---------------------------------------------------------------------------

def _build_file_tree(
    root: Path,
    *,
    lang_counts: dict[str, int],
    notes: list[str],
    all_files: list[str] | None = None,
    ignored: list[str] | None = None,
) -> list[str]:
    """Return a flat ASCII listing capped at :data:`MAX_TREE_ENTRIES`.

    Also populates the optional ``all_files`` (every non-sensitive path,
    not just the first 250) and ``ignored`` (sensitive basenames) lists
    so callers can drive the phase-5 file selector off them.
    """
    lines: list[str] = []
    sensitive_hit: set[str] = set()
    tree_full = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        # The leading dot-folder we DO want to surface: .modelbridge.
        for d in [".modelbridge"]:
            if (Path(dirpath) / d).is_dir() and d not in dirnames:
                dirnames.append(d)

        rel_dir = Path(dirpath).relative_to(root)
        # Inside .modelbridge/ prune the tooling-state subdirs (cache/,
        # logs/, sessions/, .backups/) — these change every run and would
        # invalidate file_tree_hash if walked. We still keep rules.md /
        # prompt.md at the .modelbridge top level.
        if rel_dir.parts and rel_dir.parts[0] == ".modelbridge":
            dirnames[:] = [d for d in dirnames if d not in MODELBRIDGE_INTERNAL_SUBDIRS]
        for fname in sorted(filenames):
            if _is_sensitive(fname):
                sensitive_hit.add(fname)
                if ignored is not None:
                    ignored.append(fname)
                continue
            rel_path = (rel_dir / fname).as_posix() if str(rel_dir) != "." else fname
            if all_files is not None:
                all_files.append(rel_path)
            # Language counting
            suffix = Path(fname).suffix.lower()
            lang = LANG_SUFFIXES.get(suffix)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            # Visible tree: cap entries but keep counting / collecting.
            if not tree_full:
                lines.append(rel_path)
                if len(lines) >= MAX_TREE_ENTRIES:
                    lines.append(f"… (file tree 截断在 {MAX_TREE_ENTRIES} 条)")
                    tree_full = True

    if sensitive_hit:
        notes.append(
            f"检测到敏感文件 (已跳过未读取): {', '.join(sorted(sensitive_hit))}"
        )
    return lines


def _is_sensitive(basename: str) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(basename, pat) for pat in SENSITIVE_FILE_PATTERNS)


# ---------------------------------------------------------------------------
# Tiny readers
# ---------------------------------------------------------------------------

def _read_text_capped(p: Path, max_chars: int) -> str | None:
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return f"[file too large: {size} bytes; first {max_chars} chars only]\n" + _read_head_chars(p, max_chars)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text) > max_chars:
        return text[:max_chars] + "\n[... truncated ...]"
    return text


def _read_head_chars(p: Path, max_chars: int) -> str:
    try:
        with p.open("rb") as f:
            chunk = f.read(max_chars * 4)  # rough byte cap
    except OSError:
        return ""
    try:
        return chunk.decode("utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Hash helpers — used by summary_cache to decide whether the cached
# ProjectSummary on disk is still valid for the current project state.
#
# All hashes are content-based (not mtime-based) so they are reproducible
# across copies of the same project on different machines.
# ---------------------------------------------------------------------------

#: Files whose content invalidates the cached project summary.
MANIFEST_FILES: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "README.md",
    "README.rst",
    "README.txt",
    "README",
)

#: Rule files whose content invalidates the cached project summary
#: (kept here, not in rules_loader, so we don't take a circular import).
RULE_FILES_FOR_HASH: tuple[str, ...] = (
    "AGENT.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".windsurfrules",
)


def _hash_paths(paths: list[str]) -> str:
    """Stable hash of a *sorted* POSIX path list. 16-char sha256 prefix."""
    blob = "\n".join(sorted(paths))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _hash_files(root: Path, names: tuple[str, ...]) -> str:
    """Hash (filename, content_or_marker) tuples for the given basenames.

    Missing files are still part of the hash (as ``<missing>``) so that
    creating a new ``AGENT.md`` correctly invalidates the cache.
    """
    h = hashlib.sha256()
    for name in names:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        try:
            data = p.read_bytes() if p.is_file() else b"<missing>"
        except OSError:
            data = b"<read-error>"
        h.update(data)
        h.update(b"\x00")
    return h.hexdigest()[:16]


def compute_file_tree_hash(project_path: Path | str) -> str:
    """Walk the project tree (no file reads) and hash the sorted path list.

    Cheap — only enumerates directory entries, never opens a file. Skips
    the same dirs / sensitive patterns as :func:`scan_project` so the
    hash matches what would end up in the summary's ``files`` list.
    """
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        return _hash_paths([])
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for d in [".modelbridge"]:
            if (Path(dirpath) / d).is_dir() and d not in dirnames:
                dirnames.append(d)
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts and rel_dir.parts[0] == ".modelbridge":
            dirnames[:] = [d for d in dirnames if d not in MODELBRIDGE_INTERNAL_SUBDIRS]
        for fname in sorted(filenames):
            if _is_sensitive(fname):
                continue
            rel_path = (rel_dir / fname).as_posix() if str(rel_dir) != "." else fname
            paths.append(rel_path)
    return _hash_paths(paths)


def compute_manifest_hash(project_path: Path | str) -> str:
    """Hash the content of manifest files (package.json / pyproject / README / ...)."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        return _hash_files(root, ())
    return _hash_files(root, MANIFEST_FILES)


def compute_rules_hash(project_path: Path | str) -> str:
    """Hash the content of project-level rule files (AGENT.md / CLAUDE.md / ...).

    Note: this is INDEPENDENT of the user-global ``~/.modelbridge/rules.md``
    — that lives elsewhere and is folded into the prompt's ``global_rules``
    section, not the project_summary cache key.
    """
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        return _hash_files(root, ())
    return _hash_files(root, RULE_FILES_FOR_HASH)


__all__ = [
    "ProjectSummary",
    "scan_project",
    "compute_file_tree_hash",
    "compute_manifest_hash",
    "compute_rules_hash",
    "SENSITIVE_FILE_PATTERNS",
    "SKIP_DIRS",
    "LANG_SUFFIXES",
    "MANIFEST_FILES",
    "RULE_FILES_FOR_HASH",
    "MODELBRIDGE_INTERNAL_SUBDIRS",
]
