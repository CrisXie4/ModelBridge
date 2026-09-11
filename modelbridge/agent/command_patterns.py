"""Bash "always" approval patterns — widen by one command prefix, not the whole tool.

Borrowed from opencode's permission design (``permission/arity.ts``): when a
bash command is approved with "always", we record a *prefix pattern* per
pipeline segment instead of arming ``run_bash`` for the whole session.
Approving ``git push`` therefore covers ``git push`` and ``git push origin
main``, but not ``git config user.name`` — "always" generalises exactly one
level down the command's own argument hierarchy.

Pattern format
--------------
A pattern is the command's identifying prefix tokens followed by ``" *"``,
e.g. ``"git push *"``, ``"npm run *"``, ``"python script.py *"``. How many
tokens form the "identifying prefix" comes from :data:`ARITY` (first-token
lookup, default 1). A compound command like ``git add . && git commit -m x``
produces one pattern per segment; a future command is auto-approved only
when **every** segment matches some recorded pattern.

The matcher is deliberately conservative:

* Unknown commands default to arity 1 (``<cmd> *``) — the operator can
  always see the widening in the pattern list.
* Splitting uses :mod:`shlex` with ``punctuation_chars`` — quoted
  ``"a && b"`` never splits, and unmatched quotes fall back to whitespace
  splitting (stricter, never looser).
* On Windows (``cmd.exe``) ``;`` is not a separator, so we may record more
  segments than cmd would execute — that only ever makes matching
  *stricter*, which is the safe direction for a widening rule.
"""

from __future__ import annotations

import shlex

__all__ = ["ARITY", "split_segments", "command_patterns", "command_matches"]


#: ``base command -> prefix token count``. ``git: 2`` means ``git <subcmd>``
#: identifies the operation. Absent commands default to 1. Multi-token
#: dispatchers (``npm run dev``) are covered by giving the base a higher
#: arity only where the second token already pins the semantics.
ARITY: dict[str, int] = {
    # VCS
    "git": 2,
    "hg": 2,
    "svn": 2,
    # Node ecosystems
    "npm": 2,
    "pnpm": 2,
    "yarn": 2,
    "bun": 2,
    "npx": 2,
    "node": 2,
    "deno": 2,
    # Python ecosystems
    "python": 2,
    "python3": 2,
    "py": 2,
    "pip": 2,
    "pip3": 2,
    "uv": 2,
    "poetry": 2,
    "conda": 2,
    "pytest": 1,
    "ruff": 2,
    "mypy": 1,
    # Compiled / build tools
    "go": 2,
    "cargo": 2,
    "rustc": 1,
    "make": 1,
    "cmake": 1,
    "gradle": 2,
    "./gradlew": 2,
    "mvn": 2,
    "dotnet": 2,
    # Containers / clusters
    "docker": 2,
    "podman": 2,
    "kubectl": 2,
    "helm": 2,
    # Process / service control
    "systemctl": 2,
    "service": 2,
    "launchctl": 2,
    # Package managers (system)
    "apt": 2,
    "apt-get": 2,
    "brew": 2,
    "winget": 2,
    "choco": 2,
    "scoop": 2,
    # Remote / transfer
    "ssh": 1,
    "scp": 1,
    "rsync": 1,
    "curl": 1,
    "wget": 1,
    # Search / inspection (read-only, wide is fine)
    "echo": 1,
    "cat": 1,
    "ls": 1,
    "dir": 1,
    "grep": 1,
    "find": 1,
    "rg": 1,
    "head": 1,
    "tail": 1,
    "wc": 1,
    "type": 1,
    "where": 1,
    "which": 1,
    "tree": 1,
    "diff": 1,
    "sort": 1,
    "uniq": 1,
    # Directory navigation
    "cd": 1,
    "pwd": 1,
    "pushd": 1,
    "popd": 1,
}

#: Tokens that terminate one simple command — everything before the token
#: belongs to the current segment, everything after starts a new one.
_SEGMENT_BREAKERS = frozenset({";", ";;", "&&", "||", "&", "|"})


def _is_punct(token: str) -> bool:
    """True for pure metacharacter tokens (``>&``, ``(``, ``2>`` …)."""
    return bool(token) and all(ch in ";|&<>()!" for ch in token)


def split_segments(command: str) -> list[list[str]]:
    """Tokenize ``command`` into per-simple-command token lists.

    Quoted metacharacters stay inside their tokens (shlex handles that);
    a shell syntax error falls back to whitespace splitting, which can
    only produce *more* segments — stricter matching, never looser.
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        tokens = list(lex)
    except ValueError:
        tokens = command.split()

    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _SEGMENT_BREAKERS:
            if current:
                segments.append(current)
                current = []
            continue
        if _is_punct(tok):
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments


def _pattern_for(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    arity = ARITY.get(tokens[0].lower(), 1)
    arity = max(1, min(arity, len(tokens)))
    return " ".join(tokens[:arity]) + " *"


def command_patterns(command: str) -> list[str]:
    """Prefix patterns to record when ``command`` is approved with "always"."""
    patterns: list[str] = []
    for segment in split_segments(command):
        pattern = _pattern_for(segment)
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    return patterns


def command_matches(command: str, patterns: list[str]) -> bool:
    """True when **every** segment of ``command`` matches a recorded pattern.

    A pattern ``"git push *"`` matches ``git push`` and any argument
    extension of it; a command with no segments (empty) never matches.
    """
    if not patterns:
        return False
    prefixes: list[tuple[str, ...]] = []
    for pattern in patterns:
        if not pattern.endswith(" *"):
            continue
        toks = tuple(pattern[:-2].split())
        if toks:
            prefixes.append(toks)
    if not prefixes:
        return False
    segments = split_segments(command)
    if not segments:
        return False
    for segment in segments:
        matched = any(
            len(segment) >= len(prefix) and tuple(segment[: len(prefix)]) == prefix
            for prefix in prefixes
        )
        if not matched:
            return False
    return True
