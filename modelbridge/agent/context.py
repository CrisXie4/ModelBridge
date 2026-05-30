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

    # Mutated by ApprovalDecision.ALWAYS so subsequent calls to the same
    # tool skip the prompt. Keyed by tool name.
    _auto_approved: set[str] = field(default_factory=set)

    def confirm(self, *, tool: str, summary: str, detail: str = "") -> bool:
        """Run the approval callback; return True if the action may proceed."""
        if tool in self._auto_approved:
            return True
        decision = self.approve(tool=tool, summary=summary, detail=detail)
        if decision == ApprovalDecision.ALWAYS:
            self._auto_approved.add(tool)
            return True
        return decision == ApprovalDecision.YES

    def resolve(self, path: str) -> Path:
        return self.policy.resolve(path, base=self.cwd)
