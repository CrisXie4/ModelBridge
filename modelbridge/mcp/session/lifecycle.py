"""Connection state machine for one MCP session.

Kept intentionally small for M0–M3: a flat enum + allowed transitions.
Reconnect/backoff is M5; this just records where a session is so the manager
can isolate failures (a ``FAILED`` session is skipped, the rest keep working).
"""

from __future__ import annotations

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


__all__ = ["SessionState", "can_transition"]
