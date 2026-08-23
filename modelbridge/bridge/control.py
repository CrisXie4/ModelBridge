"""Local control channel — let the CLI drive the browser via the host.

Native Messaging is browser-initiated: Chrome owns the host's stdio. To let a
separate ``mbridge`` process drive the page, the host (while the side panel is
open) also listens on a loopback TCP socket. A CLI client connects, authenticates
with a per-session token, and submits a chat turn. The host runs that turn with
the **same** engine + BrowserBridge, routing:

* model output (delta / assistant / done / error) -> the CLI socket
* tool_call (DOM ops)                              -> stdout -> the extension
* approval prompts                                 -> the CLI socket (terminal)

Tool results still arrive from the extension over stdin and are delivered to the
turn's bridge by the host's main loop; approval answers arrive over the CLI
socket and are delivered here. Both reach the same ``pending_router``.

Frames use the same length-prefixed JSON as the stdio protocol, over the socket.
The endpoint (port + token) is published to ``~/.modelbridge/bridge_endpoint.json``.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from typing import TYPE_CHECKING, Any

from ..utils import get_app_dir
from . import protocol as P
from .browser_bridge import DEFAULT_TOOL_TIMEOUT

if TYPE_CHECKING:
    from .host import Host

ENDPOINT_FILE = "bridge_endpoint.json"
CONTROL_FILE = "bridge_control.json"

# Connecting must be quick (host is local), but a single exec can legitimately
# take a long time — e.g. `navigate` waits for the page to finish loading (up to
# ~60s in the extension), and the host's per-tool timeout is DEFAULT_TOOL_TIMEOUT.
# The client read timeout must exceed that, or a slow page load is misreported as
# "timed out" (and the late result is dropped). Add margin on top of the host cap.
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = DEFAULT_TOOL_TIMEOUT + 30.0


class ControlConnectionError(Exception):
    """The CLI couldn't reach a running host (disabled, or side panel closed)."""


def endpoint_path():
    return get_app_dir() / ENDPOINT_FILE


def control_config_path():
    return get_app_dir() / CONTROL_FILE


# ---------------------------------------------------------------------------
# Opt-in state (CLI linkage is OFF by default)
# ---------------------------------------------------------------------------

def load_control_config() -> dict[str, Any]:
    """Return ``{"enabled": bool, "token": str}`` (disabled if file missing)."""
    p = control_config_path()
    if not p.exists():
        return {"enabled": False, "token": ""}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"enabled": bool(d.get("enabled")), "token": str(d.get("token") or "")}
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "token": ""}


def set_control(*, enabled: bool, token: str | None = None) -> dict[str, Any]:
    """Persist the opt-in state. Generates a token when enabling without one."""
    cur = load_control_config()
    tok = token if token is not None else cur.get("token") or ""
    if enabled and not tok:
        tok = secrets.token_hex(16)
    data = {"enabled": enabled, "token": tok}
    p = control_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return data


# ---------------------------------------------------------------------------
# Server side (runs inside the host process)
# ---------------------------------------------------------------------------

class ControlServer(threading.Thread):
    def __init__(self, host: "Host") -> None:
        super().__init__(daemon=True, name="bridge-control")
        self._host = host
        self._log = host._log
        # NOTE: the auth token is intentionally NOT captured here. The opt-in
        # config is read fresh on every connection so ``mbridge bridge on`` /
        # ``off`` takes effect immediately, without restarting this host.
        self._sock: socket.socket | None = None
        self._stopped = threading.Event()

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.listen(5)
        except OSError as e:
            self._log.warning("bridge.control bind failed: %s", e)
            return
        self._sock = sock
        port = sock.getsockname()[1]
        self._write_endpoint(port)
        self._log.info("bridge.control listening 127.0.0.1:%d", port)
        while not self._stopped.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        try:
            endpoint_path().unlink()
        except OSError:
            pass

    def _write_endpoint(self, port: int) -> None:
        # Token is NOT written here — it lives in the opt-in config that both
        # the host and the CLI read. The endpoint only advertises the port.
        data = {"port": port, "pid": os.getpid()}
        try:
            p = endpoint_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data), encoding="utf-8")
            if os.name != "nt":
                os.chmod(p, 0o600)
        except OSError as e:
            self._log.warning("bridge.control endpoint write failed: %s", e)

    def _handle(self, conn: socket.socket) -> None:
        # ``Any``: socket.makefile("rwb") is a BufferedRWPair, which typeshed
        # doesn't consider a BinaryIO even though it duck-types one.
        stream: Any = conn.makefile("rwb")
        write_lock = threading.Lock()

        def send(msg: dict[str, Any]) -> None:
            with write_lock:
                try:
                    P.write_message(stream, msg)
                except (OSError, ValueError):
                    pass

        try:
            try:
                first = P.read_message(stream)
            except P.ProtocolError:
                return
            # Fresh opt-in read per connection: ``bridge on``/``off`` applies
            # without a host restart. Disabled → same auth-failed close as a
            # bad token (loopback-only; no side channel is needed).
            cfg = load_control_config()
            token = str(cfg.get("token") or "")
            if not cfg.get("enabled") or not token:
                send(P.error(id=None, message="命令行联动未启用 (mbridge bridge on)"))
                return
            # Constant-time token compare (defence-in-depth; the server is
            # loopback-only). str(...) coercion keeps both operands str so
            # compare_digest can't raise on a missing/non-string token.
            if (
                not first
                or first.get("type") != P.T_AUTH
                or not secrets.compare_digest(str(first.get("token") or ""), token)
            ):
                send(P.error(id=None, message="auth failed"))
                return

            # Acknowledge with the same ready payload the panel gets.
            send(self._host._ready_message())

            # The agent runs in the external `mbridge` process; this server is a
            # pure relay for single browser-tool execs. Each `exec` runs in a
            # thread so this reader stays free to receive the tool_result the
            # relay blocks on (arrives from the *extension* over stdin).
            while not self._stopped.is_set():
                try:
                    msg = P.read_message(stream)
                except P.ProtocolError as e:
                    send(P.error(id=None, message=f"protocol error: {e}"))
                    continue
                if msg is None:
                    break
                if msg.get("type") == P.T_EXEC:
                    threading.Thread(
                        target=self._do_exec, args=(msg, send), daemon=True
                    ).start()
                elif msg.get("type") == P.T_CANCEL:
                    router = self._host.pending_router
                    if router is not None:
                        router.cancel()
        finally:
            # Close the buffered stream (releases its io refs → closes the
            # socket fd); the explicit conn.close() is then a harmless no-op.
            try:
                stream.close()
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def _do_exec(self, msg: dict[str, Any], send) -> None:
        rid = msg.get("id")
        res = self._host.relay_exec(str(msg.get("name") or ""), msg.get("args") or {})
        send(
            {
                "type": P.T_EXEC_RESULT,
                "id": rid,
                "ok": bool(res.get("ok")),
                "content": str(res.get("content", "")),
            }
        )


# ---------------------------------------------------------------------------
# Client side — the browser bridge used by the `mbridge` agent (REPL / one-shot)
# ---------------------------------------------------------------------------

def _read_endpoint() -> dict[str, Any]:
    p = endpoint_path()
    if not p.exists():
        raise ControlConnectionError(
            "未找到宿主端点。请在浏览器里打开 ModelBridge 侧边栏（宿主随它启动）。"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ControlConnectionError(f"读取宿主端点失败: {e}") from e


def probe_endpoint(*, timeout: float = 0.35) -> bool:
    """True when the published control port accepts connections.

    Distinguishes a live host from a stale ``bridge_endpoint.json`` left by a
    crashed/closed host — the file's presence alone used to report "connected".
    """
    try:
        ep = _read_endpoint()
    except ControlConnectionError:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(ep["port"])), timeout=timeout):
            return True
    except (OSError, ValueError, KeyError):
        return False


def control_status() -> dict[str, Any]:
    """Full diagnostic snapshot for ``mbridge bridge status`` / ``bridge on``.

    Includes the app dir so an ``MBRIDGE_HOME`` / different-user mismatch
    (``bridge on`` and the REPL disagreeing on where ``bridge_control.json``
    lives — the classic "已开启却显示未启用" cause) is visible at a glance.
    """
    cfg = load_control_config()
    ep: dict[str, Any] | None = None
    alive = False
    try:
        ep = _read_endpoint()
        alive = probe_endpoint()
    except ControlConnectionError:
        pass
    return {
        "enabled": bool(cfg.get("enabled")),
        "token_present": bool(cfg.get("token")),
        "app_dir": str(get_app_dir()),
        "config_path": str(control_config_path()),
        "endpoint": ep,
        "host_alive": alive,
    }


class RemoteBrowserBridge:
    """Client-side BrowserBridge: relays each browser tool call to the host.

    Satisfies the ``AgentContext.browser_bridge`` protocol (``.call``) so the
    existing browser tools work unchanged inside the ``mbridge`` agent. The
    agent loop runs locally; only the DOM action crosses to the extension via
    the host's control socket. Write-tool approval happens *before* this — in
    the agent's normal terminal y/N/a prompt — so no approval crosses the wire.

    One persistent connection is reused across the session; it auto-reconnects
    if the side panel was closed and reopened.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: socket.socket | None = None
        self._stream: Any = None
        self._seq = 0

    def available(self) -> tuple[bool, str]:
        """Precheck for a friendly startup message. (enabled, reason).

        Probes the actual port so a stale endpoint file (host crashed or
        panel closed) doesn't report "connected".
        """
        cfg = load_control_config()
        if not cfg.get("enabled") or not cfg.get("token"):
            return False, "命令行联动未启用 (mbridge bridge on)"
        if not probe_endpoint():
            return False, "宿主未运行 (请打开浏览器侧边栏)"
        return True, "已连接"

    def _ensure_conn(self) -> None:
        if self._conn is not None:
            return
        cfg = load_control_config()
        if not cfg.get("enabled") or not cfg.get("token"):
            raise ControlConnectionError(
                "命令行联动未启用。先运行 mbridge bridge on 并打开侧边栏。"
            )
        ep = _read_endpoint()
        try:
            conn = socket.create_connection(("127.0.0.1", int(ep["port"])), timeout=_CONNECT_TIMEOUT)
        except OSError as e:
            raise ControlConnectionError(
                "连接宿主失败（侧边栏可能已关闭）。请打开侧边栏后重试。"
            ) from e
        # The connect deadline is short, but subsequent exec reads must tolerate
        # slow page loads — otherwise navigate/click-with-navigation time out and
        # the eventual result is lost. (None would also work since the host always
        # replies within its tool timeout, but a finite cap is safer.)
        conn.settimeout(_READ_TIMEOUT)
        # ``Any``: same BufferedRWPair-vs-BinaryIO typeshed mismatch as above.
        stream: Any = conn.makefile("rwb")
        P.write_message(stream, {"type": P.T_AUTH, "token": cfg["token"]})
        ack = P.read_message(stream)
        if not ack or ack.get("type") == P.T_ERROR:
            conn.close()
            raise ControlConnectionError("宿主拒绝连接（token 失效？请重开侧边栏）。")
        self._conn, self._stream = conn, stream

    def call(self, name: str, args: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                self._ensure_conn()
            except ControlConnectionError as e:
                return {"ok": False, "content": str(e)}
            self._seq += 1
            rid = f"exec-{self._seq}"

            # The blocking read runs in a worker thread so the caller's thread
            # (the REPL) can take Ctrl-C while a slow tool — e.g. navigate
            # waiting on a page load — is in flight. On interrupt we tell the
            # host to cancel, drop the connection, and re-raise so the REPL can
            # abort just this turn instead of hanging or dying.
            holder: dict[str, Any] = {}
            done = threading.Event()

            def work():
                try:
                    holder["res"] = self._exec_blocking(rid, name, args)
                finally:
                    done.set()

            threading.Thread(target=work, daemon=True, name="browser-exec").start()
            try:
                while not done.wait(0.2):
                    pass
            except KeyboardInterrupt:
                self._cancel_remote()
                self._reset()
                raise
            return holder.get("res", {"ok": False, "content": "无结果"})

    def _exec_blocking(self, rid: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            P.write_message(
                self._stream,
                {"type": P.T_EXEC, "id": rid, "name": name, "args": args or {}},
            )
            while True:
                msg = P.read_message(self._stream)
                if msg is None:
                    self._reset()
                    return {"ok": False, "content": "与宿主的连接断开。"}
                if msg.get("type") == P.T_EXEC_RESULT and msg.get("id") == rid:
                    return {
                        "ok": bool(msg.get("ok", True)),
                        "content": str(msg.get("content", "")),
                    }
                # ignore any other frame (e.g. a stray ready)
        except (OSError, ValueError, P.ProtocolError) as e:
            self._reset()
            return {"ok": False, "content": f"浏览器联动通信失败: {e}"}

    def _cancel_remote(self) -> None:
        """Best-effort: tell the host to abort the in-flight exec so it releases
        its turn lock (otherwise the next browser action would report busy)."""
        try:
            if self._stream is not None:
                P.write_message(self._stream, {"type": P.T_CANCEL})
        except (OSError, ValueError):
            pass

    def _reset(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except OSError:
            pass
        self._conn = None
        self._stream = None

    def close(self) -> None:
        with self._lock:
            self._reset()


__all__ = [
    "ControlServer",
    "ControlConnectionError",
    "RemoteBrowserBridge",
    "endpoint_path",
    "control_config_path",
    "load_control_config",
    "set_control",
    "probe_endpoint",
    "control_status",
]
