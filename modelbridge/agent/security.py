"""Path security policy for the agent.

Two layers of defence:

1. **Allowlist** — every file path the agent touches must resolve under one
   of ``security.allowed_project_dirs`` (or the explicit ``--cwd`` passed
   to the CLI when no allowlist is configured).
2. **Sensitive-file blocklist** — basenames / path components that we
   refuse to read or write even inside the allowlist
   (``.env``, ``id_rsa``, ``.ssh`` …).

The policy is enforced *after* resolving symlinks, so a symlink under
the project dir can't escape the box.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_app_config


class PathDenied(Exception):
    """Raised when the agent tries to touch a path the policy refuses."""


@dataclass
class PathPolicy:
    """Filesystem access policy for agent tools."""

    allowed_dirs: list[Path] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, *, extra_cwd: Path | None = None) -> "PathPolicy":
        """Build a policy from ``~/.modelbridge/config.yaml``.

        If the user has no ``allowed_project_dirs`` configured, fall back
        to ``extra_cwd`` (typically the CLI's current working directory)
        so the agent has *something* to work with — but only that one
        directory.
        """
        cfg = load_app_config()
        allowed: list[Path] = []
        for d in cfg.security.allowed_project_dirs:
            p = Path(d).expanduser().resolve()
            if p.exists():
                allowed.append(p)
        if extra_cwd is not None:
            resolved_cwd = extra_cwd.resolve()
            if resolved_cwd not in allowed:
                allowed.append(resolved_cwd)
        return cls(
            allowed_dirs=allowed,
            blocked_patterns=list(cfg.security.block_sensitive_files),
        )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, path: str | Path, *, base: Path | None = None) -> Path:
        """Resolve a (possibly relative) path against ``base`` (or first allowed dir).

        Raises :class:`PathDenied` if the resolved path is outside every
        allowed directory, or matches a blocked pattern.
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            anchor = base or (self.allowed_dirs[0] if self.allowed_dirs else Path.cwd())
            p = anchor / p
        try:
            # ``strict=False`` so we can resolve paths for files that don't
            # exist yet (write_file). Existing symlinks still resolve.
            resolved = p.resolve(strict=False)
        except OSError as e:
            raise PathDenied(f"无法解析路径 {p}: {e}") from e

        self._check_allowed(resolved)
        self._check_blocked(resolved)
        return resolved

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _check_allowed(self, resolved: Path) -> None:
        if not self.allowed_dirs:
            raise PathDenied(
                "未配置任何 allowed_project_dirs，且未传入 --cwd；"
                "请在 ~/.modelbridge/config.yaml 的 security.allowed_project_dirs 中加入项目目录。"
            )
        for root in self.allowed_dirs:
            try:
                resolved.relative_to(root)
                return
            except ValueError:
                continue
        roots = ", ".join(str(r) for r in self.allowed_dirs)
        raise PathDenied(
            f"路径 {resolved} 不在允许的项目目录之内 (允许: {roots})。"
        )

    def _check_blocked(self, resolved: Path) -> None:
        # Match against each path component and against the basename.
        components = list(resolved.parts)
        for pattern in self.blocked_patterns:
            if any(fnmatch.fnmatch(part, pattern) for part in components):
                raise PathDenied(
                    f"路径 {resolved} 命中敏感文件规则 {pattern!r}，已拒绝。"
                    "如需放行，请编辑 config.yaml security.block_sensitive_files。"
                )

    def describe(self) -> str:
        roots = ", ".join(str(r) for r in self.allowed_dirs) or "<empty>"
        blocks = ", ".join(self.blocked_patterns) or "<empty>"
        return f"allowed=[{roots}]  blocked=[{blocks}]"
