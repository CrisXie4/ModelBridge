"""stdio transport: spawn an MCP server subprocess and talk newline-JSON.

Design notes (Windows-aware — see the project's subprocess quirks):

* We spawn the command **directly** (no ``shell=True``), so there's no
  ``cmd /c`` wrapper to leak a grandchild. Still, on close/timeout we escalate
  to a process-tree kill (``taskkill /F /T`` on Windows) just like
  ``executor.runner._kill_tree``.
* ``select()`` doesn't work on Windows pipes, so a **daemon reader thread**
  drains stdout line-by-line into a ``queue.Queue``. ``receive`` pulls from the
  queue with a timeout — uniform behaviour on every OS.
* MCP requires stdout to carry *only* JSON frames; servers must log to stderr.
  We drain stderr on its own daemon thread into a ring buffer for diagnostics.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from typing import Any

from ..errors import MCPConnectError, MCPTimeoutError, MCPTransportError
from ..logging import log_lifecycle, scrub_env
from ..protocol.codec import decode_message, encode_line
from ..protocol.messages import IncomingMessage
from .base import Transport

_IS_WINDOWS = sys.platform.startswith("win")
_EOF = object()  # sentinel pushed onto the queue when stdout closes

# Environment variable *names* that look credential-bearing. An MCP server is
# third-party, possibly untrusted code (the README examples npx-fetch servers),
# so it must not inherit the host's API keys / tokens just by being spawned.
# A server that genuinely needs a secret declares it explicitly in its `env:`
# config block, which is re-applied on top of the scrubbed base.
#
# The keyword set alone covers every provider key this project uses
# (DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / MOONSHOT_API_KEY / ZHIPU_API_KEY /
# MINIMAX_API_KEY / OPENAI_API_KEY …) and the standard cloud creds
# (AWS_SECRET_ACCESS_KEY, AZURE_CLIENT_SECRET, GOOGLE_APPLICATION_CREDENTIALS).
# We deliberately do NOT match bare "SESSION" (would drop DBUS_SESSION_BUS_ADDRESS
# and other benign desktop vars) nor an OPENAI_/GOOGLE_ prefix (would drop
# OPENAI_BASE_URL — a real self-hosted-gateway config — while the *_API_KEY is
# already caught by KEY).
_SECRET_ENV_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTHORIZATION)",
    re.IGNORECASE,
)
# Cloud-credential prefixes whose secrets are already keyword-caught; kept as a
# belt-and-suspenders net for oddly-named creds under these namespaces.
_SECRET_ENV_PREFIXES = ("AWS_", "AZURE_")


def build_child_env(overrides: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Host env minus credential-looking vars, then the server's explicit env.

    Returns ``(env, dropped_names)``. ``dropped_names`` is for diagnostics only
    (never log the values). Explicit ``overrides`` always win — they let a
    server opt back into a specific secret it legitimately needs.
    """
    dropped: list[str] = []
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        if _SECRET_ENV_RE.search(name) or name.upper().startswith(_SECRET_ENV_PREFIXES):
            dropped.append(name)
            continue
        env[name] = value
    env.update(overrides)  # explicit declarations re-add anything needed
    return env, sorted(dropped)


class StdioTransport(Transport):
    def __init__(
        self,
        *,
        server_id: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.server_id = server_id
        self._command = command
        self._args = list(args)
        self._env_overrides = dict(env or {})
        self._cwd = cwd or None
        self._proc: subprocess.Popen[str] | None = None
        self._inbox: "queue.Queue[Any]" = queue.Queue()
        self._stderr_tail: list[str] = []
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._closed = False

    # ------------------------------------------------------------------
    def start(self, *, timeout: float) -> None:
        env, dropped = build_child_env(self._env_overrides)
        popen_kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line-buffered
        )
        if self._cwd:
            popen_kwargs["cwd"] = self._cwd
        if not _IS_WINDOWS:
            # Own process group so a tree-kill hits descendants on POSIX.
            popen_kwargs["start_new_session"] = True

        try:
            self._proc = subprocess.Popen([self._command, *self._args], **popen_kwargs)
        except (OSError, ValueError) as e:
            raise MCPConnectError(
                f"无法启动 MCP server 进程: {self._command}",
                server_id=self.server_id,
                hint="确认 command 在 PATH 中（如需 npx，请先装 Node.js）",
                raw=str(e),
            ) from e

        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_reader.start()
        log_lifecycle(
            self.server_id, "spawned",
            f"cmd={self._command} env={scrub_env(self._env_overrides)} "
            f"dropped_secrets={len(dropped)}",
        )

    # ------------------------------------------------------------------
    def _pump_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                if line.strip():
                    self._inbox.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._inbox.put(_EOF)

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                self._stderr_tail.append(line.rstrip("\n"))
                if len(self._stderr_tail) > 50:
                    self._stderr_tail.pop(0)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------
    def send(self, frame: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPTransportError("transport 未启动", server_id=self.server_id)
        if self._proc.poll() is not None:
            raise MCPTransportError(
                "MCP server 进程已退出",
                server_id=self.server_id,
                hint=self._stderr_hint(),
            )
        try:
            self._proc.stdin.write(encode_line(frame))
            self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise MCPTransportError(
                "写入 server stdin 失败（管道可能已断）",
                server_id=self.server_id,
                hint=self._stderr_hint(),
                raw=str(e),
            ) from e

    # ------------------------------------------------------------------
    def receive(self, *, timeout: float) -> IncomingMessage:
        try:
            item = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise MCPTimeoutError(
                f"等待 server 响应超时（{timeout:.0f}s）",
                server_id=self.server_id,
                hint="server 可能卡住；可调大 request_timeout 或检查 server 日志",
            ) from None
        if item is _EOF:
            raise MCPTransportError(
                "server 关闭了 stdout（进程可能已崩溃）",
                server_id=self.server_id,
                hint=self._stderr_hint(),
            )
        return decode_message(item)

    # ------------------------------------------------------------------
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        # Try a graceful stdin close first, then escalate to a tree-kill.
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
        except OSError:
            pass
        # Unblock and reap the reader threads so repeated reconnects don't
        # leak them. Closing the child's read pipes forces a blocked
        # ``for line in stdout`` to raise OSError/ValueError, which the pump
        # loops already swallow — this handles the case where a surviving
        # grandchild kept the write end open past the tree-kill.
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        for t in (self._reader, self._stderr_reader):
            if t is not None and t.is_alive():
                t.join(timeout=1)
        log_lifecycle(self.server_id, "closed")

    # ------------------------------------------------------------------
    @staticmethod
    def _kill_tree(proc: "subprocess.Popen[str]") -> None:
        """Best-effort kill of the process and any descendants (mirrors executor)."""
        if _IS_WINDOWS:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        else:
            import signal as _sig
            try:
                os.killpg(proc.pid, _sig.SIGKILL)  # type: ignore[attr-defined]  # POSIX-only
            except (OSError, ProcessLookupError):
                try:
                    proc.kill()
                except OSError:
                    pass

    def _stderr_hint(self) -> str:
        if not self._stderr_tail:
            return "server 没有 stderr 输出"
        tail = " / ".join(self._stderr_tail[-3:])
        return f"server stderr 末尾: {tail}"


__all__ = ["StdioTransport"]
