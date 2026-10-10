"""AgentContext — shared state for one agent session.

Bundles the things every tool needs:

* :class:`PathPolicy` for filesystem access checks
* The session ``cwd`` (used as the relative-path anchor)
* An approval callback so destructive tools can ask the user before
  touching anything (or skip the prompt entirely under ``--yes``)
* A flag for opt-in bash execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from .security import PathPolicy


class ApprovalDecision(str, Enum):
    YES = "yes"
    NO = "no"
    ALWAYS = "always"  # auto-approve future calls in this session


class ApprovalFn(Protocol):
    def __call__(
        self,
        *,
        tool: str,
        summary: str,
        detail: str = "",
        reason: str = "",
        save_pattern: str | None = None,
        auto: bool = False,
    ) -> "ApprovalDecision | tuple[ApprovalDecision, str]": ...
    """``reason`` is the LLM's justification for the action (shown to the user).
    ``save_pattern`` persists an "always" decision. ``auto`` enables LLM safety judgement.

    May return a ``(decision, feedback)`` tuple: feedback is free text the user
    typed when rejecting ("别动这个文件，改用 X 方案") — :meth:`AgentContext.confirm`
    stores it on ``last_feedback`` so the tool can hand it back to the model as
    part of the rejection error (a steering signal, not a dead end)."""


class BrowserBridge(Protocol):
    """Round-trips a browser-tool call back to the side-panel extension.

    Implemented by :class:`modelbridge.bridge.browser_bridge.BrowserBridge`.
    Typed structurally here so :mod:`agent` never imports :mod:`bridge`.
    Returns ``{"ok": bool, "content": str}``.
    """

    def call(self, name: str, args: dict, *, timeout: float | None = None) -> dict: ...


def auto_yes(*, tool: str, summary: str, detail: str = "", reason: str = "",
              save_pattern: str | None = None, auto: bool = False) -> ApprovalDecision:
    """Approval callback that says yes to everything (`--yes`)."""
    return ApprovalDecision.YES


def auto_no(*, tool: str, summary: str, detail: str = "", reason: str = "",
            save_pattern: str | None = None, auto: bool = False) -> ApprovalDecision:
    return ApprovalDecision.NO


@dataclass
class AgentContext:
    policy: PathPolicy
    cwd: Path
    approve: ApprovalFn = field(default=auto_no)
    allow_bash: bool = False
    # True when the active model has capabilities.vision — lets ``read_file``
    # return an image as an image block (vs. a "binary image" text note).
    model_is_vision: bool = False
    # True when the active model also accepts video input (capabilities.video,
    # on top of vision) — ``read_file`` returns a ``video_url`` block.
    model_is_video: bool = False
    # Set by the browser side-panel host so browser tools can reach the page.
    # ``None`` in the CLI / REPL (no browser tools registered there).
    browser_bridge: BrowserBridge | None = None

    # Mutated by ApprovalDecision.ALWAYS so subsequent calls to the same
    # tool skip the prompt. Keyed by tool name.
    _auto_approved: set[str] = field(default_factory=set)
    # run_bash "always" approvals, pattern-scoped (opencode-style): each
    # entry is a command prefix pattern like "git push *" recorded by
    # command_patterns.command_patterns(). A future command auto-approves
    # only when every one of its pipeline segments matches a pattern —
    # approving "git push" never widens to "git config". See
    # ``bash_allowed`` / ``approve_bash``.
    _bash_patterns: list[str] = field(default_factory=list)
    # When True, every tool confirmation runs LLM auto-judge first (safe → auto-yes,
    # unsafe → fall through to human prompt). Set via /auto slash command.
    _auto_mode: bool = False
    # Outcome of the most recent confirm() call: the raw decision (useful to
    # distinguish YES from ALWAYS when callers handle the widening themselves,
    # as run_bash does for pattern recording) and optional user feedback text
    # from a rejection. Both reset at the start of every confirm().
    last_decision: ApprovalDecision | None = None
    last_feedback: str | None = None
    # True when a subagent tool is registered for this session — lets tools
    # tailor truncation hints ("delegate to a subagent") to what's available.
    delegable: bool = False
    # 文件读取账本（U8，对齐 ZCode readFileState）：绝对路径字符串 →
    # (st_mtime_ns, st_size)。read_file 记账；str_replace / write_file 命中
    # "读过但已被外部改动"的文件时报错请重读；重复读同一未变文件返回短
    # stub 省上下文。未读过的文件行为完全不变。仅记文本读取（图片/视频
    # 走独立消费路径不记账）。
    file_reads: dict[str, tuple[int, int]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 文件读取账本（U8）
    # ------------------------------------------------------------------

    @staticmethod
    def file_fingerprint(path: Path) -> tuple[int, int] | None:
        """磁盘指纹 (st_mtime_ns, st_size)；stat 失败返回 None。"""
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def file_read_state(self, path: Path) -> tuple[int, int] | None:
        """该路径的记账指纹；从未读过返回 None。"""
        return self.file_reads.get(str(path))

    def note_file_read(self, path: Path) -> None:
        """记录/刷新一次文件读取（stat 失败则静默跳过，不影响主流程）。"""
        fp = self.file_fingerprint(path)
        if fp is not None:
            self.file_reads[str(path)] = fp

    def is_externally_modified(self, path: Path) -> bool:
        """True = 读过，但磁盘上的 (mtime, size) 已与记账时不一致。

        未读过的文件返回 False —— 账本只加防护，不改变未读文件的行为。
        """
        recorded = self.file_reads.get(str(path))
        if recorded is None:
            return False
        return self.file_fingerprint(path) != recorded

    def confirm(
        self,
        *,
        tool: str,
        summary: str,
        detail: str = "",
        reason: str = "",
        group: str | None = None,
        allow_always: bool = True,
        pattern_key: str | None = None,
        auto: bool = False,
    ) -> bool:
        """Run the approval callback; return True if the action may proceed.

        ``reason`` is the LLM's justification ("why I'm clicking this") — shown
        on the approval card and fed to the LLM safety judge.

        ``group`` lets several related tools share one "always" decision: pass
        the same group (e.g. ``"browser_write"``) on ``click`` / ``fill`` /
        ``navigate`` so approving one with ALWAYS auto-approves them all this
        session. Defaults to the tool's own name (per-tool, the old behaviour).

        ``allow_always=False`` is for tools where every single call must be
        seen by the user: an ALWAYS decision is honoured for *this* call only
        and never remembered. (No current tool uses this; run_bash moved to
        ``allow_always=True`` — one "always" covers the session.)

        ``pattern_key``, if set, is passed to the approval callback so it can
        persist the "always" choice to disk (used by ``spawn_subagent`` for
        permanent approvals).

        ``auto=True`` enables LLM-based safety judgement before showing the
        prompt: if the tiny model says "safe", the action proceeds without
        interrupting the user.
        """
        key = group or tool
        self.last_decision = None
        self.last_feedback = None
        if key in self._auto_approved:
            return True
        # /auto mode forces auto-judge on for every confirmation
        effective_auto = auto or self._auto_mode
        decision = self.approve(
            tool=tool, summary=summary, detail=detail, reason=reason,
            save_pattern=pattern_key, auto=effective_auto,
        )
        # A callback may return (decision, feedback) — feedback is free text
        # the user typed while rejecting; tools surface it to the model so a
        # rejection can steer the next attempt instead of dead-ending it.
        feedback = ""
        if isinstance(decision, tuple):
            decision, feedback = decision
        self.last_decision = decision
        self.last_feedback = (feedback or "").strip() or None
        if decision == ApprovalDecision.ALWAYS:
            if allow_always:
                self._auto_approved.add(key)
            return True
        return decision == ApprovalDecision.YES

    # ------------------------------------------------------------------
    # run_bash pattern-scoped "always" (command_patterns.py)
    # ------------------------------------------------------------------

    def bash_allowed(self, command: str) -> bool:
        """True when every pipeline segment of ``command`` matches a recorded
        bash "always" pattern."""
        from .command_patterns import command_matches

        return command_matches(command, self._bash_patterns)

    def approve_bash(self, command: str) -> list[str]:
        """Record prefix patterns for ``command`` after an ALWAYS decision.

        Returns the patterns added (also appended to ``_bash_patterns``).
        """
        from .command_patterns import command_patterns

        added = command_patterns(command)
        for pattern in added:
            if pattern not in self._bash_patterns:
                self._bash_patterns.append(pattern)
        return added

    def resolve(self, path: str) -> Path:
        return self.policy.resolve(path, base=self.cwd)
