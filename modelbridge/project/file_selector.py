"""Keyword-driven file selector.

Given a user question + a :class:`ProjectSummary`, pick the 5–10 files
most likely to help the model answer. The heuristic is deliberately
simple (string-match + weighted scoring) — phase-5 doesn't ship a
semantic index. The win comes from *not* dumping every file into the
prompt.

Scoring (per file)
------------------

* Each hit on a query token in the **basename** adds 6 points.
* Each hit on a query token elsewhere in the path adds 3.
* Each hit on a **topic keyword** (login / api / db / config / …) in the
  basename adds 8; in the path adds 4.
* README / package.json / pyproject.toml etc. get a +5 boost, but
  the **non-README scores must clear a threshold** before the README
  is reported as the "answer" — so generic queries surface README
  first while specific queries don't drown it in code.
* Entrypoints (main.py / app.py / index.ts …) get +4.
* Files under the user-mentioned directory (if any path-like token
  in the query) get +3 same-folder bonus.
* Files inside ``tests/`` or ``test/`` are deprioritised by -2 unless
  the query mentions "test"/"测试".

The selector returns at most :data:`DEFAULT_TOP_N` files, never
exceeds :data:`HARD_CAP`, and always includes the README + the first
matching entrypoint when the project has them (they're free
information to ground the model).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable

from .scanner import ProjectSummary


DEFAULT_TOP_N: int = 8
HARD_CAP: int = 10
MIN_SCORE_KEEP: int = 4  # below this we don't return the file


# ---------------------------------------------------------------------------
# Topic keywords — Chinese + English. Order doesn't matter; all hits sum.
# ---------------------------------------------------------------------------

# Each entry: (topic_label, path-substrings to match, query-trigger tokens).
# A topic fires when ANY trigger appears in the query. When it fires,
# every file whose path contains any of the path-substrings gets the
# topic-bonus.
_TOPICS: list[tuple[str, list[str], list[str]]] = [
    # auth / login
    ("auth", ["login", "auth", "session", "signin", "signup", "oauth", "jwt", "token"],
        ["登录", "注册", "auth", "login", "session", "鉴权", "认证", "权限"]),
    # api / routes
    ("api", ["api", "route", "router", "controller", "handler", "endpoint", "view"],
        ["接口", "api", "endpoint", "路由", "route", "router", "controller"]),
    # database / model
    ("db", ["db", "database", "model", "schema", "orm", "migration", "repository"],
        ["数据库", "db", "database", "model", "schema", "orm", "数据"]),
    # config / settings / env
    ("config", ["config", "settings", "setup", "env", "constants"],
        ["配置", "setting", "config", "env"]),
    # tests
    ("test", ["test", "spec", "__tests__"],
        ["测试", "test", "spec"]),
    # ui / pages / components
    ("ui", ["pages", "page", "components", "component", "view", "ui", "screen"],
        ["页面", "组件", "ui", "page", "component", "view"]),
    # docs / readme
    ("docs", ["readme", "docs", "doc", "guide", "tutorial", "changelog"],
        ["介绍", "文档", "readme", "doc", "guide", "项目是什么", "干嘛"]),
    # deploy / docker / ci
    ("deploy", ["dockerfile", "docker-compose", "deploy", ".github/workflows", "ci"],
        ["部署", "deploy", "docker", "ci", "k8s"]),
]

# Known important / boost files (basename → boost).
_IMPORTANT_BOOST: dict[str, int] = {
    "README.md": 5, "README.rst": 5, "README.txt": 5, "README": 5,
    "package.json": 5, "pyproject.toml": 5, "requirements.txt": 4,
    "Cargo.toml": 5, "go.mod": 5, "pom.xml": 5, "build.gradle": 4,
    "Dockerfile": 3, "docker-compose.yml": 3, "docker-compose.yaml": 3,
    "Makefile": 3,
}

# Always-considered entrypoint basenames.
_ENTRYPOINT_BOOST: dict[str, int] = {
    "main.py": 4, "app.py": 4, "manage.py": 4, "server.py": 4,
    "index.js": 4, "index.ts": 4, "server.js": 4, "server.ts": 4,
    "main.go": 4, "main.rs": 4,
    "Main.java": 4, "Program.cs": 4,
}

_BINARY_LIKE_SUFFIXES: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".wav", ".ogg", ".webm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".class", ".jar",
})


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SelectedFile:
    """One file the selector recommends reading."""

    path: str
    """POSIX-relative path under ``ProjectSummary.project_path``."""
    reason: str
    """Human-readable explanation (shown by ``--show-files``)."""
    score: int
    """Final score; higher = more relevant."""


@dataclass
class SelectionResult:
    """Top-N selection plus diagnostics."""

    files: list[SelectedFile] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    """Topic labels that fired (e.g. ``["auth", "api"]``)."""
    query_tokens: list[str] = field(default_factory=list)
    """Tokens extracted from the query for keyword matching."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+|[一-鿿]+|\d+")


def _tokenise_query(query: str) -> list[str]:
    """Lower-case alnum + CJK runs. Drops 1-char fragments."""
    if not query:
        return []
    tokens = _TOKEN_RE.findall(query)
    out: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        t = t.lower()
        if len(t) < 2:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _is_text_like(path: str) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    return suffix not in _BINARY_LIKE_SUFFIXES


def _basename(p: str) -> str:
    return PurePosixPath(p).name


def _fires_topic(query_lower: str, triggers: Iterable[str]) -> bool:
    return any(t.lower() in query_lower for t in triggers)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_files(
    query: str,
    summary: ProjectSummary,
    *,
    top_n: int = DEFAULT_TOP_N,
) -> SelectionResult:
    """Pick the top files for ``query`` against ``summary.files``."""
    top_n = max(1, min(int(top_n), HARD_CAP))

    if not summary.files:
        return SelectionResult()

    q_lower = (query or "").lower()
    tokens = _tokenise_query(query)

    # Resolve topics that fire for this query.
    active_topics: list[tuple[str, list[str]]] = []
    fired_labels: list[str] = []
    for label, path_subs, triggers in _TOPICS:
        if _fires_topic(q_lower, triggers):
            active_topics.append((label, path_subs))
            fired_labels.append(label)

    # Path-token hint: if the query contains a slash or a folder-looking
    # token, give files inside that folder a small bonus.
    folder_hints: list[str] = []
    for tok in tokens:
        if "/" in tok or tok in {"src", "lib", "app", "pages", "components", "tests", "test"}:
            folder_hints.append(tok)

    # README and first entrypoint paths (always considered).
    readme_path: str | None = None
    entrypoint_paths: list[str] = []
    for f in summary.files:
        base = _basename(f)
        if readme_path is None and base.lower().startswith("readme"):
            readme_path = f
        if base in _ENTRYPOINT_BOOST:
            entrypoint_paths.append(f)

    scored: dict[str, tuple[int, list[str]]] = {}
    for path in summary.files:
        if not _is_text_like(path):
            continue
        score = 0
        reasons: list[str] = []
        base = _basename(path)
        base_lower = base.lower()
        path_lower = path.lower()

        # Per-token matches
        for tok in tokens:
            in_base = tok in base_lower
            in_path = (not in_base) and (tok in path_lower)
            if in_base:
                score += 6
                reasons.append(f"basename 含 '{tok}'")
            elif in_path:
                score += 3
                reasons.append(f"路径含 '{tok}'")

        # Topic matches
        for label, path_subs in active_topics:
            hit_base = any(sub in base_lower for sub in path_subs)
            hit_path = (not hit_base) and any(sub in path_lower for sub in path_subs)
            if hit_base:
                score += 8
                reasons.append(f"topic={label} (basename)")
            elif hit_path:
                score += 4
                reasons.append(f"topic={label} (路径)")

        # Important boost (README / manifest)
        if base in _IMPORTANT_BOOST:
            score += _IMPORTANT_BOOST[base]
            reasons.append("important file")

        # Entrypoint boost
        if base in _ENTRYPOINT_BOOST:
            score += _ENTRYPOINT_BOOST[base]
            reasons.append("entrypoint")

        # Folder hint
        if folder_hints:
            for hint in folder_hints:
                if hint in path_lower and hint not in base_lower:
                    score += 3
                    reasons.append(f"位于提到的目录 '{hint}'")
                    break

        # Test-deprioritisation when user isn't asking about tests
        if "test" not in q_lower and "测试" not in q_lower:
            if "/test/" in f"/{path_lower}/" or "/tests/" in f"/{path_lower}/" or base_lower.startswith("test_"):
                score -= 2
                reasons.append("非测试问题，降权")

        if score > 0:
            scored[path] = (score, reasons)

    # Build ranked list, then force-include README + first entrypoint if free.
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1][0], kv[0]))
    selected: list[SelectedFile] = []
    seen_paths: set[str] = set()

    def _push(path: str, score: int, reason: str) -> None:
        if path in seen_paths or len(selected) >= top_n:
            return
        seen_paths.add(path)
        selected.append(SelectedFile(path=path, reason=reason, score=score))

    for path, (score, reasons) in ranked:
        if score < MIN_SCORE_KEEP:
            break
        _push(path, score, "; ".join(reasons[:3]) or "score>0")

    # Ground the model — README + one entrypoint, even if they didn't score.
    if readme_path and readme_path not in seen_paths and len(selected) < top_n:
        s, r = scored.get(readme_path, (0, []))
        _push(readme_path, s, "; ".join(r[:2]) if r else "README (always-included)")
    if entrypoint_paths and len(selected) < top_n:
        ep = entrypoint_paths[0]
        if ep not in seen_paths:
            s, r = scored.get(ep, (0, []))
            _push(ep, s, "; ".join(r[:2]) if r else "entrypoint (always-included)")

    return SelectionResult(
        files=selected,
        topics=fired_labels,
        query_tokens=tokens,
    )


__all__ = [
    "DEFAULT_TOP_N",
    "HARD_CAP",
    "MIN_SCORE_KEEP",
    "SelectedFile",
    "SelectionResult",
    "select_files",
]
