"""Bridge browser-tool calls from the worker thread to the extension.

A browser tool (e.g. ``read_page``) runs inside :func:`run_agent_turn` on the
host's worker thread, but the work must happen in the page. :class:`BrowserBridge`
makes that synchronous from the tool's point of view:

1. ``call(name, args)`` sends a ``tool_call`` frame and blocks on a per-request
   :class:`threading.Event`.
2. The host's main thread keeps reading; when the extension replies with a
   ``tool_result`` (or ``approval_result``) frame, :meth:`deliver` matches it by
   ``requestId`` and wakes the blocked tool.

One :class:`BrowserBridge` is created per turn and registered as the host's
``pending_router`` so inbound result frames find it.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from . import protocol as P

SendFn = Callable[[dict[str, Any]], None]

# 侧边栏在页面加载中会等到加载完成 (上限 60s, 见 sidepanel.js LOAD_WAIT_MS) 再执行
# 并回复，所以这里必须留出比那更长的余量，否则慢页面会被误判为"无响应"。
DEFAULT_TOOL_TIMEOUT = 90.0
DEFAULT_APPROVAL_TIMEOUT = 300.0


class BrowserBridge:
    def __init__(self, send: SendFn, *, approval_send: SendFn | None = None, turn_id: str = "") -> None:
        # ``send`` carries tool_call frames to whoever executes DOM ops (the
        # extension). ``approval_send`` carries approval prompts to whoever
        # confirms — the side panel for panel turns, the CLI terminal for
        # CLI-initiated turns. Defaults to the same sink (panel case).
        self._send = send
        self._approval_send = approval_send or send
        self.turn_id = turn_id
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}  # rid -> {event, msg}
        self._seq = 0
        self._cancelled = threading.Event()

    def _next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"{self.turn_id or 'r'}-{self._seq}"

    # ------------------------------------------------------------------
    # Outbound (called from the worker thread, blocks)
    # ------------------------------------------------------------------

    def _await(
        self, rid: str, frame: dict[str, Any], timeout: float, send: SendFn
    ) -> dict[str, Any] | None:
        event = threading.Event()
        # Check-cancel and register atomically under the same lock that
        # cancel() takes. Otherwise a cancel() landing between an unlocked
        # pre-check and registration would set _cancelled but not find this
        # rid in _pending, so the worker would block for the full timeout
        # (up to 5 min) instead of aborting promptly.
        with self._lock:
            if self._cancelled.is_set():
                return None
            self._pending[rid] = {"event": event, "msg": None}
        send(frame)
        got = event.wait(timeout)
        with self._lock:
            entry = self._pending.pop(rid, None)
        if not got or entry is None:
            return None
        return entry["msg"]

    def call(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Run a browser tool in the page. Returns ``{"ok": bool, "content": str}``."""
        rid = self._next_id()
        frame = P.tool_call(id=self.turn_id, request_id=rid, name=name, args=args or {})
        msg = self._await(rid, frame, timeout or DEFAULT_TOOL_TIMEOUT, self._send)
        if msg is None:
            if self._cancelled.is_set():
                return {"ok": False, "content": f"已取消: {name}"}
            return {"ok": False, "content": f"浏览器工具超时或无响应: {name}"}
        return {"ok": bool(msg.get("ok", True)), "content": str(msg.get("content", ""))}

    def request_approval(
        self, *, tool: str, summary: str, detail: str = "", timeout: float | None = None
    ) -> str:
        """Ask the side panel to confirm. Returns ``yes`` | ``no`` | ``always``."""
        rid = self._next_id()
        frame = P.approval(
            id=self.turn_id, request_id=rid, tool=tool, summary=summary, detail=detail
        )
        msg = self._await(rid, frame, timeout or DEFAULT_APPROVAL_TIMEOUT, self._approval_send)
        if msg is None:
            return "no"  # timeout / cancel = deny (safe default)
        decision = str(msg.get("decision", "no")).lower()
        return decision if decision in ("yes", "no", "always") else "no"

    # ------------------------------------------------------------------
    # Inbound (called from the host main thread)
    # ------------------------------------------------------------------

    def deliver(self, msg: dict[str, Any]) -> None:
        """Match an inbound result frame to a waiting request by ``requestId``."""
        rid = str(msg.get("requestId") or "")
        with self._lock:
            entry = self._pending.get(rid)
            if entry is None:
                return
            entry["msg"] = msg
            entry["event"].set()

    def cancel(self) -> None:
        """Abort all in-flight requests (the panel sent ``cancel``)."""
        self._cancelled.set()
        with self._lock:
            for entry in self._pending.values():
                entry["event"].set()


__all__ = ["BrowserBridge", "DEFAULT_TOOL_TIMEOUT", "DEFAULT_APPROVAL_TIMEOUT"]
