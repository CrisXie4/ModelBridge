"""Live agent state — the bridge between the running REPL and the web UI.

The CLI REPL and the FastAPI admin server are separate processes, so the
agent's runtime state (todos, context usage, current topic) is published to
a single JSON file at ``~/.modelbridge/live.json``. The web server reads it;
the REPL writes it.

Three pieces live here:

* :class:`TodoStore` — the in-memory todo list the AI mutates via
  :class:`~modelbridge.agent.tools.todo_tool.TodoTool`.
* :class:`LiveStateWriter` — holds a live reference to the :class:`Session`
  and model resolver; :meth:`flush` recomputes context stats and rewrites
  ``live.json``.
* :func:`read_live_state` / :func:`clear_live_state` — the read side the
  web server uses.

Writes are atomic (temp file + ``os.replace``) so the reader never sees a
half-written file, and never raise — a failed write is logged and skipped,
telemetry must never crash the agent.
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..context.windows import context_window_for, estimate_session_tokens
from ..models import ModelEntry
from ..schemas import ChatMessage
from ..utils import get_app_dir, now_iso

LIVE_FILENAME = "live.json"
_TOPIC_MAX = 100  # truncate the "current topic" so it fits one line in the UI


def get_live_file() -> Any:
    """Return the path to ``~/.modelbridge/live.json`` (does not ensure it exists)."""
    return get_app_dir() / LIVE_FILENAME


# ---------------------------------------------------------------------------
# TodoStore
# ---------------------------------------------------------------------------

# Status vocabulary: low cardinality so the UI can render deterministically.
TODO_STATUSES = ("pending", "in_progress", "done")
TODO_PRIORITIES = ("low", "normal", "high")


@dataclass
class TodoItem:
    id: int
    content: str
    status: str = "pending"        # pending | in_progress | done
    priority: str = "normal"       # low | normal | high
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TodoStore:
    """Thread-safe in-memory todo list.

    The store keeps an optional ``on_change`` callback (typically the
    :class:`LiveStateWriter.flush`) so every AI mutation immediately
    republishes ``live.json`` — the web UI sees a new todo the moment the
    agent adds it, not at the next turn boundary.
    """

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._items: list[TodoItem] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._on_change = on_change

    def _bump(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001, S110 — never break the agent over telemetry
                pass

    def add(self, content: str, priority: str = "normal") -> TodoItem:
        priority = priority if priority in TODO_PRIORITIES else "normal"
        with self._lock:
            item = TodoItem(id=self._next_id, content=content, priority=priority)
            self._next_id += 1
            self._items.append(item)
        self._bump()
        return item

    def update_status(self, todo_id: int, status: str) -> TodoItem | None:
        status = status if status in TODO_STATUSES else "pending"
        with self._lock:
            for it in self._items:
                if it.id == todo_id:
                    it.status = status
                    it.updated_at = now_iso()
                    item = it
                    break
            else:
                return None
        self._bump()
        return item

    def remove(self, todo_id: int) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [it for it in self._items if it.id != todo_id]
            removed = len(self._items) < before
        if removed:
            self._bump()
        return removed

    def clear(self, status: str | None = None) -> int:
        with self._lock:
            before = len(self._items)
            if status is None:
                self._items = []
            else:
                self._items = [it for it in self._items if it.status != status]
            removed = before - len(self._items)
        if removed:
            self._bump()
        return removed

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [it.to_dict() for it in self._items]

    def summary(self) -> dict[str, int]:
        with self._lock:
            total = len(self._items)
            done = sum(1 for it in self._items if it.status == "done")
            in_prog = sum(1 for it in self._items if it.status == "in_progress")
            pending = sum(1 for it in self._items if it.status == "pending")
        return {"total": total, "done": done, "in_progress": in_prog, "pending": pending}


# ---------------------------------------------------------------------------
# LiveStateWriter
# ---------------------------------------------------------------------------

ModelResolver = Callable[[], str]


class LiveStateWriter:
    """Publish the agent's live state to ``live.json``.

    Construct once in the CLI with the live :class:`Session` and a model
    resolver (so ``/model`` swaps are reflected), then call :meth:`flush`
    whenever the state changes — typically via the ``on_user_echo`` and
    ``on_turn_done`` REPL callbacks, plus the :class:`TodoStore.on_change`.
    """

    def __init__(
        self,
        *,
        session: Any,
        todo_store: TodoStore,
        model_resolver: ModelResolver,
        entry_lookup: Callable[[str], ModelEntry | None],
        cwd: str,
    ) -> None:
        self._session = session
        self._todo_store = todo_store
        self._model_resolver = model_resolver
        self._entry_lookup = entry_lookup
        self._cwd = cwd
        self._topic: str | None = None
        self._lock = threading.Lock()

    def set_topic(self, text: str) -> None:
        t = (text or "").strip().replace("\n", " ")
        self._topic = t[:_TOPIC_MAX] + ("…" if len(t) > _TOPIC_MAX else "") or None

    def _context_stats(self, model_name: str) -> dict[str, Any]:
        messages: list[ChatMessage] = list(getattr(self._session, "messages", []))
        used = estimate_session_tokens(messages) if messages else 0
        entry = self._entry_lookup(model_name)
        window = context_window_for(entry) if entry is not None else 0
        pct = round((used / window) * 100, 1) if window else 0.0
        return {
            "used_tokens": used,
            "context_window": window,
            "used_pct": pct,
            "free_tokens": max(0, window - used) if window else 0,
            "message_count": len(messages),
        }

    def snapshot(self) -> dict[str, Any]:
        model_name = self._model_resolver()
        todos = self._todo_store.to_list()
        summary = self._todo_store.summary()
        return {
            "model": model_name,
            "cwd": self._cwd,
            "topic": self._topic,
            "status": "idle",
            "context": self._context_stats(model_name),
            "todos": todos,
            "todo_summary": summary,
            "updated_at": now_iso(),
            "pid": os.getpid(),
        }

    def flush(self, *, status: str | None = None) -> None:
        data = self.snapshot()
        if status is not None:
            data["status"] = status
        try:
            self._write_atomic(data)
        except Exception:  # noqa: BLE001, S110 — live.json is best-effort telemetry
            pass

    def _write_atomic(self, data: dict[str, Any]) -> None:
        import json

        path = get_live_file()
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp file in the same dir, then atomically replace.
            fd, tmp = tempfile.mkstemp(
                prefix=".live.", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise


# ---------------------------------------------------------------------------
# Read side (used by the web server)
# ---------------------------------------------------------------------------

_STALE_SECONDS = 30


def read_live_state() -> dict[str, Any] | None:
    """Read and lightly post-process ``live.json``.

    Returns ``None`` when no REPL has ever published state, or when the file
    is unreadable. Adds a derived ``is_stale`` flag (true when no update in
    :data:`_STALE_SECONDS` seconds — the REPL is probably not running).
    """
    import json

    path = get_live_file()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — unreadable/missing → treat as offline
        return None

    updated = data.get("updated_at")
    if isinstance(updated, str):
        try:
            dt = datetime.fromisoformat(updated)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            data["age_seconds"] = round(age, 1)
            data["is_stale"] = age > _STALE_SECONDS
        except Exception:  # noqa: BLE001 — malformed timestamp → mark stale
            data["age_seconds"] = None
            data["is_stale"] = True
    else:
        data["age_seconds"] = None
        data["is_stale"] = True
    return data


def clear_live_state() -> None:
    """Remove ``live.json`` (called on REPL exit so the UI shows 'offline')."""
    path = get_live_file()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


__all__ = [
    "TODO_PRIORITIES",
    "TODO_STATUSES",
    "LiveStateWriter",
    "TodoItem",
    "TodoStore",
    "clear_live_state",
    "get_live_file",
    "read_live_state",
]
