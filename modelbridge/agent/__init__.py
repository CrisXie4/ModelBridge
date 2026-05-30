"""Agent package — Claude-Code-style multi-turn with file/shell tools.

CLI surface lives in :func:`modelbridge.cli` under the ``mbridge agent``
subcommand. Everything in here is intentionally independent of the
CLI so it can also be reused by the future Web Server / MCP layers.
"""

from .context import AgentContext, ApprovalDecision
from .loop import AgentResult, run_agent_turn, run_interactive
from .security import PathDenied, PathPolicy
from .session import Session

__all__ = [
    "AgentContext",
    "ApprovalDecision",
    "AgentResult",
    "run_agent_turn",
    "run_interactive",
    "PathDenied",
    "PathPolicy",
    "Session",
]
