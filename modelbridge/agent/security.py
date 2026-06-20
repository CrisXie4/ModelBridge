"""Path security policy for the agent.

Two layers of defence:

1. **Allowlist** — every file path the agent touches must resolve under one
   of ``security.allowed_project_dirs`` (or the explicit ``--cwd`` passed
   to the CLI when no allowlist is configured).
2. **Sensitive-file blocklist** — basenames / path components that we
   refuse to read or write even inside the allowlist
   (``.env``, ``.env.*``, ``*.pem``, ``*.key``, ``id_rsa``, ``.ssh``,
   ``.npmrc``, ``*credentials*`` …). The baseline comes from the same
   :data:`~modelbridge.project.scanner.SENSITIVE_FILE_PATTERNS` glob set
   used by the project scanner, ``@file`` mentions and ``mbridge edit``,
   so all read/write paths share one non-disableable blocklist; the
   user's ``security.block_sensitive_files`` only *adds* to it.

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
    #: User/config patterns — matched against *every* path component, so a
    #: configured directory name (e.g. ``.ssh``) blocks its whole subtree.
    blocked_patterns: list[str] = field(default_factory=list)
    #: Baseline sensitive-file globs (the scanner's ``SENSITIVE_FILE_PATTERNS``)
    #: — matched against the **basename only**, exactly like the scanner /
    #: ``@file`` / ``mbridge edit``. Kept separate from ``blocked_patterns``
    #: so a broad glob like ``*_secret*`` can't over-block an ancestor dir
    #: (a project cloned into ``~/aws-credentials/`` stays readable).
    baseline_patterns: list[str] = field(default_factory=list)

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
        # Pull in the scanner's baseline globs so the agent's read/write
        # tools refuse the same secrets that @file and ``mbridge edit``
        # already refuse. Importing here (not at module top) avoids a
        # config→scanner import cycle. The baseline cannot be weakened by a
        # user overriding block_sensitive_files.
        from ..project.scanner import SENSITIVE_FILE_PATTERNS

        return cls(
            allowed_dirs=allowed,
            blocked_patterns=list(cfg.security.block_sensitive_files),
            baseline_patterns=list(SENSITIVE_FILE_PATTERNS),
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
        components = list(resolved.parts)
        # User/config patterns: match any path component (a configured dir
        # name like ``.ssh`` blocks the whole subtree).
        for pattern in self.blocked_patterns:
            if any(fnmatch.fnmatch(part, pattern) for part in components):
                raise PathDenied(
                    f"路径 {resolved} 命中敏感文件规则 {pattern!r}，已拒绝。"
                    "如需放行，请编辑 config.yaml security.block_sensitive_files。"
                )
        # Baseline scanner globs are *file* patterns — match the basename
        # only (same as project.scanner), so broad wildcards can't over-block
        # an ancestor directory whose name happens to contain the substring.
        basename = resolved.name
        for pattern in self.baseline_patterns:
            if fnmatch.fnmatch(basename, pattern):
                raise PathDenied(
                    f"路径 {resolved} 命中内置敏感文件规则 {pattern!r}，已拒绝。"
                    "（此为不可关闭的密钥保护基线，与项目扫描器一致。）"
                )

    def describe(self) -> str:
        roots = ", ".join(str(r) for r in self.allowed_dirs) or "<empty>"
        blocks = ", ".join(self.blocked_patterns) or "<none>"
        extra = f" +{len(self.baseline_patterns)} 内置敏感模式" if self.baseline_patterns else ""
        return f"allowed=[{roots}]  blocked=[{blocks}]{extra}"
