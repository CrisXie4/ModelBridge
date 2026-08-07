"""Live session state — what the running REPL is doing right now.

Reads ``~/.modelbridge/live.json`` (published by the CLI's
:class:`~modelbridge.agent.live_state.LiveStateWriter`). Returns ``online=false``
when no REPL has published state (file missing / stale), so the web UI can
distinguish "no agent running" from "agent mid-turn".
"""

from __future__ import annotations

from fastapi import APIRouter

from ...agent.live_state import read_live_state

router = APIRouter(prefix="/session", tags=["session"])


@router.get("/live")
def live_session() -> dict:
    data = read_live_state()
    if data is None:
        return {
            "online": False,
            "status": "offline",
            "topic": None,
            "model": None,
            "cwd": None,
            "context": None,
            "todos": [],
            "todo_summary": {"total": 0, "done": 0, "in_progress": 0, "pending": 0},
            "updated_at": None,
            "age_seconds": None,
            "is_stale": True,
        }
    data["online"] = bool(data.get("is_stale") is False)
    return data
