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
    def __call__(self, *, tool: str, summary: str, detail: str = "") -> ApprovalDecision: ...


class BrowserBridge(Protocol):
    """Round-trips a browser-tool call back to the side-panel extension.

    Implemented by :class:`modelbridge.bridge.browser_bridge.BrowserBridge`.
    Typed structurally here so :mod:`agent` never imports :mod:`bridge`.
    Returns ``{"ok": bool, "content": str}``.
    """

    def call(self, name: str, args: dict, *, timeout: float | None = None) -> dict: ...


def auto_yes(*, tool: str, summary: str, detail: str = "") -> ApprovalDecision:  # noqa: ARG001
    """Approval callback that says yes to everything (`--yes`)."""
    return ApprovalDecision.YES


def auto_no(*, tool: str, summary: str, detail: str = "") -> ApprovalDecision:  # noqa: ARG001
    return ApprovalDecision.NO


@dataclass
class AgentContext:
    policy: PathPolicy
    cwd: Path
    approve: ApprovalFn = field(default=auto_no)
    allow_bash: bool = False
    # Set by the browser side-panel host so browser tools can reach the page.
    # ``None`` in the CLI / REPL (no browser tools registered there).
    browser_bridge: BrowserBridge | None = None

    # Mutated by ApprovalDecision.ALWAYS so subsequent calls to the same
    # tool skip the prompt. Keyed by tool name.
    _auto_approved: set[str] = field(default_factory=set)

    def confirm(self, *, tool: str, summary: str, detail: str = "", group: str | None = None,
                allow_always: bool = True) -> bool:
        """Run the approval callback; return True if the action may proceed.

        ``group`` lets several related tools share one "always" decision: pass
        the same group (e.g. ``"browser_write"``) on ``click`` / ``fill`` /
        ``navigate`` so approving one with ALWAYS auto-approves them all this
        session. Defaults to the tool's own name (per-tool, the old behaviour).

        ``allow_always=False`` is for high-risk tools (e.g. ``run_bash``): an
        ALWAYS decision is honoured for *this* call only and never remembered,
        so the user sees every shell command. ``--yes`` (auto-approve) still
        applies — that's an explicit non-interactive opt-in — but the safety
        net there is the command policy gate, not the prompt.
        """
        key = group or tool
        if key in self._auto_approved:
            return True
        decision = self.approve(tool=tool, summary=summary, detail=detail)
        if decision == ApprovalDecision.ALWAYS:
            if allow_always:
                self._auto_approved.add(key)
            return True
        return decision == ApprovalDecision.YES

    def resolve(self, path: str) -> Path:
        return self.policy.resolve(path, base=self.cwd)
