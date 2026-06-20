"""Whitelist / blacklist policy for shell commands.

The executor runs commands with ``shell=True``, which means an unvetted
``pytest; rm -rf /`` would be just as effective as the intended
``pytest``. This module is the safety net.

Three layers:

1. **No shell metacharacters** — ``;``, ``&&``, ``||``, ``|``, ```` ` ````,
   ``$(``, ``>``, ``>>``, ``<``, newline. Without these, the shell can
   only execute the one program the user typed.
2. **Allowlist on first token** — only ``pytest`` / ``python`` / ``npm``
   / ``go`` / ``cargo`` / … may be the program. Empty default-deny.
3. **Denylist on first token + substring** — ``rm``, ``shutdown``,
   ``curl``, ``ssh`` … and the rare cases where an allowlisted command
   carries a banned flag combination (``rm -rf``, ``kill -9``).

The allowlist may be **extended** (not overridden) via
``~/.modelbridge/config.yaml: executor.allowed_commands``. The denylist
is not user-configurable.
"""

from __future__ import annotations

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

_FORBIDDEN_METACHARS: tuple[str, ...] = (
    ";",
    "&",        # cmd.exe unconditional separator *and* POSIX background.
                #   Substring matches also cover "&&". Without it,
                #   `python -c pass & curl evil` smuggles a second command
                #   past the first-token allowlist.
    "|",        # substring match also covers "||".
    "`",
    "$(",
    ">",
    "<",
    "\n",
    "\r",
)


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
    forbidden_metachars: tuple[str, ...] = field(default_factory=lambda: _FORBIDDEN_METACHARS)

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

        # Layer 1: forbid shell metacharacters so the program token is the
        # only thing executed.
        for ch in self.forbidden_metachars:
            if ch in command:
                raise CommandRejected(
                    f"禁止复合命令 / 重定向 (检测到 {ch!r})。"
                    "执行器只允许单条命令，不能用 ;|&<>`$( 等元字符串接。"
                    "注意：即使这些字符出现在引号内的字面量里也会被拦截，"
                    "请改写命令以避免使用它们。"
                )

        # Layer 3a: substring patterns that survive layer 1 (e.g. "rm -rf"
        # is still inside a single command if someone tries to whitelist
        # "rm").
        lowered = command.lower()
        for needle in self.deny_substrings:
            if needle.lower() in lowered:
                raise CommandRejected(f"命中黑名单子串: {needle!r}")

        # Tokenise. ``shlex.split(..., posix=False)`` keeps quotes intact
        # but still handles whitespace, which is enough for first-token
        # extraction on both POSIX and Windows.
        try:
            tokens = shlex.split(command, posix=False)
        except ValueError as e:
            raise CommandRejected(f"命令解析失败: {e}") from e
        if not tokens:
            raise CommandRejected("命令为空。")

        first = _normalize_program(tokens[0])

        # Layer 3b: explicit deny on the program itself.
        if first in self.deny_tokens:
            raise CommandRejected(f"命令 {first!r} 在黑名单内。")

        # Layer 2: must be on the allowlist.
        if first not in self.allow_tokens:
            allowed = ", ".join(sorted(self.allow_tokens))
            raise CommandRejected(
                f"命令 {first!r} 未在白名单内。"
                f"\n可用白名单: {allowed}"
                "\n如需追加，在 ~/.modelbridge/config.yaml 的 executor.allowed_commands 添加。"
            )


def _normalize_program(token: str) -> str:
    """Strip quotes, path prefix, and ``.exe`` suffix; lower-case."""
    t = token.strip().strip('"').strip("'")
    base = PurePath(t).name
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base.lower()


__all__ = ["CommandPolicy", "CommandRejected"]
