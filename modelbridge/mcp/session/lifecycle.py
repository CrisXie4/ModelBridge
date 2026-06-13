"""Connection state machine + reconnect policy for one MCP session.

The flat enum + allowed transitions record where a session is so the manager
can isolate failures (a ``FAILED`` session is skipped, the rest keep working).
:class:`ReconnectPolicy` (M5) describes how aggressively the manager retries a
dead session: exponential backoff, capped, a fixed number of attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SessionState(str, Enum):
    NEW = "new"                # constructed, not yet connected
    CONNECTING = "connecting"  # transport started, handshake in flight
    READY = "ready"            # handshake done, capabilities known
    FAILED = "failed"          # connect/handshake error — isolated
    CLOSED = "closed"          # shut down cleanly


# Allowed forward transitions. Anything not listed is a programming error.
_ALLOWED: dict[SessionState, set[SessionState]] = {
    SessionState.NEW: {SessionState.CONNECTING, SessionState.CLOSED},
    SessionState.CONNECTING: {SessionState.READY, SessionState.FAILED, SessionState.CLOSED},
    SessionState.READY: {SessionState.FAILED, SessionState.CLOSED},
    SessionState.FAILED: {SessionState.CLOSED, SessionState.CONNECTING},
    SessionState.CLOSED: set(),
}


def can_transition(src: SessionState, dst: SessionState) -> bool:
    return dst in _ALLOWED.get(src, set())


@dataclass(frozen=True)
class ReconnectPolicy:
    """How the manager retries a dead session (M5)."""

    max_attempts: int = 2
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delays(self) -> list[float]:
        """Backoff delay before each attempt: base, base*2, base*4, … capped."""
        return [
            min(self.base_delay * (2 ** i), self.max_delay)
            for i in range(max(0, self.max_attempts))
        ]


__all__ = ["SessionState", "can_transition", "ReconnectPolicy"]
