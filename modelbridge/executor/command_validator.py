"""Whitelist / denylist policy for shell commands (``mbridge run``).

The executor runs commands with ``shell=True``, so the policy must guarantee
that **every program** the shell will invoke is allowlisted — not just the
first token of the line. Three layers:

1. **Quote-aware segment split** on ``;`` ``&`` ``|`` (incl. ``&&`` /
   ``||``) and newlines — compound lines, pipes and redirects are allowed;
   every segment is validated independently. Command substitution
   (`` ` `` / ``$(``) is banned outright: it executes without any separator
   token, so no split can contain it.
2. **Allowlist on the first token of every segment** — only ``pytest`` /
   ``python`` / ``npm`` / ``go`` / ``cargo`` / … may run. Empty default-deny.
3. **Denylist on first token + substring** — ``rm``, ``shutdown``,
   ``curl``, ``ssh`` … and flag combos like ``rm -rf``.

The allowlist may be **extended** (not overridden) via
``~/.modelbridge/config.yaml: executor.allowed_commands``. The denylist
is not user-configurable.

The agent-side ``run_bash`` tool deliberately does NOT route through this
policy — the per-command user confirmation is its gate (see
``agent/tools/bash_tool.py``).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePath

from ..config import load_app_config


_DEFAULT_ALLOW: tuple[str, ...] = (
    "pytest",
    "python",
    "python3",
    "py",
    "npm",
    "pnpm",
    "yarn",
    "node",
    "go",
    "cargo",
    "rustc",
    "make",
    "ruff",
    "mypy",
    "black",
    "tsc",
    "jest",
    "vitest",
)

_DEFAULT_DENY: tuple[str, ...] = (
    "rm",
    "rmdir",
    "del",
    "erase",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "mkfs",
    "dd",
    "kill",
    "killall",
    "taskkill",
    "scp",
    "sftp",
    "ssh",
    "rsync",
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "sudo",
    "su",
    "chmod",
    "chown",
    "format",
    "fdisk",
    "mkpart",
)

_DEFAULT_DENY_SUBSTRINGS: tuple[str, ...] = (
    "rm -rf",
    "rm -fr",
    "kill -9",
    "kill -KILL",
    ":(){:|:&};:",
    "/dev/sda",
    "/dev/nvme",
    "mkfs.",
    "> /dev/",
)

# Command substitution executes without any separator token, so it can never
# be whitelist-vetted segment by segment. Banned as a raw substring (even
# inside quotes — the shell would expand it there too on POSIX).
_SUBSTITUTION_SEQS: tuple[str, ...] = ("`", "$(")

# `2>&1` / `1>&2` style fd-merge redirections: the `&` here is part of the
# redirect, not a separator. Neutralise it before the segment split so
# `pytest 2>&1 | tee log` isn't torn apart at the `&`.
_REDIR_MERGE = re.compile(r"(?<=[0-9])>&(?=[0-9])")

# Top-level separators recognised by both cmd.exe and POSIX sh.
_SEPARATORS = frozenset(";|&")


class CommandRejected(Exception):
    """Raised by :meth:`CommandPolicy.validate` when a command is blocked."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CommandPolicy:
    """Decides whether a shell command line may run."""

    allow_tokens: frozenset[str] = field(default_factory=lambda: frozenset(_DEFAULT_ALLOW))
    deny_tokens: frozenset[str] = field(default_factory=lambda: frozenset(_DEFAULT_DENY))
    deny_substrings: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_DENY_SUBSTRINGS)

    @classmethod
    def from_config(cls) -> "CommandPolicy":
        """Load policy, extending the built-in allowlist from user config.

        The denylist is intentionally **not** loaded from config — a user
        config override could otherwise neutralise the safety net.
        """
        cfg = load_app_config()
        extra = {t.strip().lower() for t in cfg.executor.allowed_commands if t and t.strip()}
        allow = frozenset(_DEFAULT_ALLOW) | extra
        return cls(allow_tokens=allow)

    def validate(self, command: str) -> None:
        """Raise :class:`CommandRejected` if ``command`` is unsafe."""
        if not command or not command.strip():
            raise CommandRejected("命令为空。")

        # Layer 1a: command substitution — outright ban (see module docstring).
        for seq in _SUBSTITUTION_SEQS:
            if seq in command:
                raise CommandRejected(
                    f"禁止命令替换 (检测到 {seq!r})。请先在命令外展开取值。"
                )

        # Layer 3a: substring patterns checked on the whole line (e.g. "rm
        # -rf" survives even if someone allowlists "rm").
        lowered = command.lower()
        for needle in self.deny_substrings:
            if needle.lower() in lowered:
                raise CommandRejected(f"命中黑名单子串: {needle!r}")

        # Layer 1b + 2 + 3b: split on top-level separators and run every
        # segment's program through deny → allow.
        segments = _split_segments(command)
        if not segments:
            raise CommandRejected("命令为空。")
        for tokens in segments:
            first = _normalize_program(tokens[0])
            if first in self.deny_tokens:
                raise CommandRejected(f"命令 {first!r} 在黑名单内。")
            if first not in self.allow_tokens:
                allowed = ", ".join(sorted(self.allow_tokens))
                raise CommandRejected(
                    f"命令 {first!r} 未在白名单内。"
                    f"\n可用白名单: {allowed}"
                    "\n如需追加，在 ~/.modelbridge/config.yaml 的 executor.allowed_commands 添加。"
                )


def _split_segments(command: str) -> list[list[str]]:
    """Split ``command`` on top-level ``;`` ``&`` ``|`` and newlines.

    Quote-aware via :mod:`shlex` ``punctuation_chars``: separators inside
    quotes (``git commit -m "fix; typo"``) stay inside the segment, and
    ``&&`` / ``||`` / runs like ``|&`` arrive as single separator tokens.
    Returns one token list per segment; empty segments are dropped.
    """
    # Newline is a separator on both shells. Mapping it to ";" pre-lex is
    # safe: a ";" that lands inside quotes is inert, so quoted multi-line
    # arguments still lex as one segment.
    text = command.replace("\r", ";").replace("\n", ";")
    # `2>&1` must not split at its "&" — neutralise before lexing.
    text = _REDIR_MERGE.sub(">_", text)

    lex = shlex.shlex(text, posix=False, punctuation_chars=";|&")
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as e:
        raise CommandRejected(f"命令解析失败: {e}") from e

    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        # An unquoted run of separator chars is its own token; a quoted one
        # keeps its quote marks and fails this test.
        if tok and set(tok) <= _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _normalize_program(token: str) -> str:
    """Strip quotes, path prefix, and ``.exe`` suffix; lower-case."""
    t = token.strip().strip('"').strip("'")
    base = PurePath(t).name
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base.lower()


__all__ = ["CommandPolicy", "CommandRejected"]
