"""LocalBridge Native Messaging host — the stdio main loop.

Threading model
---------------
* **Main thread** owns the stdin read loop. It frames-in one message at a
  time and routes by ``type``.
* A single **worker thread** runs the current agent turn (so the main thread
  stays free to receive ``tool_result`` / ``approval_result`` / ``cancel``
  *while* a turn is in flight — required once browser tools round-trip in
  Stage 2).
* All writes to stdout go through :meth:`Host.send`, guarded by a lock, so the
  worker thread and main thread never interleave a frame.

Only one turn runs at a time; a ``chat`` that arrives while busy is rejected
with an ``error`` frame rather than queued.

stdout discipline: nothing but frames is ever written to stdout — every log
line goes to the rotating file logger (stderr-safe). See
:mod:`modelbridge.bridge.protocol`.
"""

from __future__ import annotations

import threading
from typing import Any, BinaryIO

from .. import __version__
from ..utils import get_logger
from . import protocol as P


def _bridge_logger():
    """Child of the project rotating file logger — never writes to stdout."""
    get_logger()
    import logging

    return logging.getLogger("modelbridge.bridge")


class Host:
    """Owns the stdio streams, the active turn, and thread-safe output."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO) -> None:
        self._in = stdin
        self._out = stdout
        self._write_lock = threading.Lock()
        self._log = _bridge_logger()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        # Set by the runner so inbound tool_result / approval_result frames can
        # be delivered to the in-flight turn (wired in Stage 2/3).
        self.pending_router: Any | None = None
        # Lazily-built engine runner (owns the persistent conversation).
        self._runner: Any | None = None
        # Serializes turns across BOTH the panel (stdio) and CLI (control
        # socket) — only one agent turn may own ``pending_router`` at a time.
        self._turn_lock = threading.Lock()
        # Local control server for CLI-initiated turns (started in serve()).
        self._control: Any | None = None

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def send(self, msg: dict[str, Any]) -> None:
        """Frame and write one message. Thread-safe; the only stdout writer."""
        with self._write_lock:
            try:
                P.write_message(self._out, msg)
            except (OSError, ValueError) as e:
                # stdout closed (browser disconnected) or message too large.
                self._log.warning("bridge.send failed: %s", e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def serve(self) -> None:
        """Read frames until EOF (browser disconnects), routing each."""
        self._log.info("bridge.host start version=%s", __version__)
        self._start_control_server()
        self.send(self._ready_message())
        try:
            while True:
                try:
                    msg = P.read_message(self._in)
                except P.ProtocolError as e:
                    self._log.warning("bridge.protocol error: %s", e)
                    self.send(P.error(id=None, message=f"protocol error: {e}"))
                    continue
                if msg is None:
                    break  # clean EOF — browser closed the port
                self._route(msg)
        finally:
            if self._control is not None:
                self._control.stop()
        self._log.info("bridge.host stop (eof)")

    def _start_control_server(self) -> None:
        """Bind the local control socket so the CLI can drive the browser.

        Opt-in: only starts when the user has run ``mbridge bridge control on``.
        Off by default — no port is opened and no endpoint is published.
        """
        try:
            from .control import ControlServer, load_control_config

            if not load_control_config().get("enabled"):
                self._log.info(
                    "bridge.control disabled (run `mbridge bridge control on` to enable)"
                )
                self._control = None
                return
            self._control = ControlServer(self)
            self._control.start()
        except Exception as e:  # noqa: BLE001 - control is optional; never block stdio
            self._log.warning("bridge.control failed to start: %s", e)
            self._control = None

    def _ready_message(self) -> dict[str, Any]:
        models: list[str] = []
        default_model: str | None = None
        try:
            from ..config import load_app_config, load_models_file

            models = [m.name for m in load_models_file().models]
            default_model = load_app_config().default_model
        except Exception as e:  # noqa: BLE001 - never let discovery crash startup
            self._log.warning("bridge.ready model discovery failed: %s", e)
        return P.ready(version=__version__, models=models, default_model=default_model)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _route(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == P.T_CHAT:
            self._on_chat(msg)
        elif mtype in (P.T_TOOL_RESULT, P.T_APPROVAL_RESULT):
            router = self.pending_router
            if router is not None:
                router.deliver(msg)
            else:
                self._log.warning("bridge: %s with no active turn", mtype)
        elif mtype == P.T_CANCEL:
            self._cancel.set()
            router = self.pending_router
            if router is not None:
                router.cancel()
        else:
            self._log.warning("bridge: unknown message type %r", mtype)
            self.send(P.error(id=msg.get("id"), message=f"unknown message type: {mtype}"))

    def _on_chat(self, msg: dict[str, Any]) -> None:
        self._cancel.clear()
        self._worker = threading.Thread(
            target=self.handle_chat, args=(msg,), daemon=True, name="bridge-turn"
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # Turn execution — serialized; routes output to the given sinks.
    # ------------------------------------------------------------------

    def run_turn(
        self,
        runner: Any,
        msg: dict[str, Any],
        *,
        reply_send: Any,
        tool_send: Any,
        approval_send: Any,
    ) -> None:
        """Run one turn under the turn lock. Used by both the panel and the
        CLI control server. If a turn is already running, reply busy."""
        turn_id = str(msg.get("id") or "")
        if not self._turn_lock.acquire(blocking=False):
            reply_send(P.error(id=turn_id, message="busy: 另一个任务正在进行中，请稍候。"))
            reply_send(P.done(id=turn_id, stopped="busy"))
            return
        try:
            runner.run(
                msg,
                reply_send=reply_send,
                tool_send=tool_send,
                approval_send=approval_send,
            )
        except Exception as e:  # noqa: BLE001 - turn boundary; surface, don't crash host
            self._log.exception("bridge.turn crashed")
            reply_send(P.error(id=turn_id, message=f"{type(e).__name__}: {e}"))
            reply_send(P.done(id=turn_id, stopped="error"))
        finally:
            self._turn_lock.release()

    def relay_exec(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Relay ONE browser tool to the extension and return its result.

        Used by the CLI control server: the agent runs in the external
        ``mbridge`` process; only the DOM action is round-tripped here. Returns
        ``{"ok": bool, "content": str}``. Serialized against panel turns.
        """
        if not self._turn_lock.acquire(blocking=False):
            return {"ok": False, "content": "busy: 浏览器正忙（侧边栏或另一会话在执行）。"}
        try:
            from .browser_bridge import BrowserBridge

            bridge = BrowserBridge(self.send, turn_id="exec")
            self.pending_router = bridge
            try:
                return bridge.call(name, args, timeout=timeout)
            finally:
                self.pending_router = None
        finally:
            self._turn_lock.release()

    def handle_chat(self, msg: dict[str, Any]) -> None:
        """Panel turn: all sinks go to stdout (the extension)."""
        if self._runner is None:
            from .session_runner import SessionRunner

            self._runner = SessionRunner(self)
        self.run_turn(
            self._runner,
            msg,
            reply_send=self.send,
            tool_send=self.send,
            approval_send=self.send,
        )


def main() -> None:
    """Entry point for ``mbridge-bridge`` / ``mbridge bridge run``."""
    stdin, stdout = P.configure_binary_stdio()
    Host(stdin, stdout).serve()


if __name__ == "__main__":  # pragma: no cover
    main()
