"""Shell command runner — the bottom layer of the executor.

This is a thin wrapper over :func:`subprocess.Popen` that:

* locks the working directory to a caller-supplied ``cwd``;
* enforces a wall-clock timeout (default 30 s);
* captures stdout and stderr separately;
* decodes child output with a **UTF-8 → OS-locale → GBK** fallback so
  tools that print in the Windows console code page (cp936/GBK on
  Chinese Windows) aren't mangled into ``�`` by a hard-coded UTF-8
  decode (only ``errors="replace"`` as a last resort);
* truncates output to ``max_output`` characters total (8 KB by default,
  matching ``agent.tools.bash_tool``);
* measures elapsed time so the CLI can show it;
* on timeout, kills the **whole process tree** — without this, on
  Windows ``shell=True`` spawns ``cmd.exe`` which spawns the real
  program, and ``proc.kill()`` only kills cmd; the grandchild keeps
  the captured pipes open and the parent blocks reading until it
  finishes naturally.

It performs **no** validation — the caller (CLI / loop) must vet the
command with :class:`CommandPolicy` first. Keeping these two concerns
separate makes the runner trivially reusable from the future ``mbridge
fix`` / ``mbridge loop`` paths.
"""

from __future__ import annotations

import locale
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


_IS_WINDOWS = sys.platform == "win32"


def _decode_child(raw: bytes) -> str:
    """Decode child-process output bytes, tolerant of the OS code page.

    Order: UTF-8 (so UTF-8 tools and mbridge-spawned output stay exact) →
    the OS preferred encoding (cp936/GBK on Chinese Windows) → GBK
    explicitly (so cp936 output still decodes even when mbridge itself
    runs in a UTF-8 locale) → UTF-8 with ``errors="replace"`` as a last
    resort. UTF-8 is tried first, so genuinely-UTF-8 text never falls
    through to a lossy code-page guess.
    """
    if not raw:
        return ""
    encs: list[str] = ["utf-8"]
    pref = locale.getpreferredencoding(False)
    if pref and pref.lower() not in ("utf-8", "utf8"):
        encs.append(pref)
    if not any(e.lower() == "gbk" for e in encs):
        encs.append("gbk")
    for enc in encs:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass
class CommandResult:
    """The outcome of a single :func:`run_command` invocation."""

    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False


def run_command(
    command: str,
    *,
    cwd: Path,
    timeout: float = 30.0,
    max_output: int = 8000,
) -> CommandResult:
    """Run ``command`` in ``cwd`` and capture its output.

    Parameters
    ----------
    command:
        Full shell command line. Pass to the platform shell verbatim
        (``cmd`` on Windows, ``/bin/sh`` elsewhere).
    cwd:
        Working directory. Caller resolves and validates this path —
        ``subprocess`` is invoked with ``cwd=str(cwd)`` so the child
        cannot escape via relative paths in the command.
    timeout:
        Wall-clock cap in seconds. On expiry the process tree is
        killed; the result has ``timed_out=True`` and ``exit_code=-1``.
    max_output:
        Total stdout+stderr ceiling, in characters. Each stream is
        independently truncated to ``max_output // 2`` so a noisy stderr
        cannot crowd out the actual error message in stdout.
    """
    start = time.perf_counter()
    timed_out = False

    # Capture raw bytes (no text/encoding) so we can apply our own
    # locale-tolerant decode; a hard-coded UTF-8 decode mangles cp936/GBK
    # tool output on Chinese Windows.
    popen_kwargs: dict = dict(
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if _IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]  # Windows-only
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(command, **popen_kwargs)
    try:
        raw_out, raw_err = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            raw_out, raw_err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            raw_out, raw_err = b"", b""
        exit_code = -1

    stdout = _decode_child(raw_out)
    stderr = _decode_child(raw_err)
    if timed_out:
        stderr = (stderr.rstrip() + f"\n[timeout after {timeout:.0f}s]").lstrip("\n")

    duration_ms = int((time.perf_counter() - start) * 1000)

    half = max(0, max_output // 2)
    truncated = False
    if stdout and len(stdout) > half:
        stdout = stdout[:half] + f"\n[... stdout truncated at {half} chars ...]"
        truncated = True
    if stderr and len(stderr) > half:
        stderr = stderr[:half] + f"\n[... stderr truncated at {half} chars ...]"
        truncated = True

    return CommandResult(
        command=command,
        stdout=stdout or "",
        stderr=stderr or "",
        exit_code=exit_code,
        duration_ms=duration_ms,
        truncated=truncated,
        timed_out=timed_out,
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    """Best-effort kill of ``proc`` and any descendants.

    On Windows ``shell=True`` spawns ``cmd /c <cmd>``; the real program
    is cmd's child. ``proc.kill()`` only signals cmd, leaving the
    grandchild alive and holding the captured pipes open. We escalate
    to ``taskkill /F /T`` to kill the tree. On POSIX, the subprocess
    is its own session leader (``start_new_session=True``), so a
    negative PID signal hits the whole group.
    """
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
    else:
        import signal as _sig
        try:
            os.killpg(proc.pid, _sig.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass


__all__ = ["CommandResult", "run_command"]

