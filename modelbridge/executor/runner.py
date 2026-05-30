"""Shell command runner — the bottom layer of the executor.

This is a thin wrapper over :func:`subprocess.Popen` that:

* locks the working directory to a caller-supplied ``cwd``;
* enforces a wall-clock timeout (default 30 s);
* captures stdout and stderr separately;
* truncates output to ``max_output`` bytes total (8 KB by default,
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

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


_IS_WINDOWS = sys.platform == "win32"


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
        Total stdout+stderr ceiling. Each stream is independently
        truncated to ``max_output // 2`` so a noisy stderr cannot crowd
        out the actual error message in stdout.
    """
    start = time.perf_counter()
    timed_out = False

    popen_kwargs: dict = dict(
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if _IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        stderr = (stderr or "").rstrip()
        stderr = (stderr + f"\n[timeout after {timeout:.0f}s]").lstrip("\n")
        exit_code = -1

    duration_ms = int((time.perf_counter() - start) * 1000)

    half = max(0, max_output // 2)
    truncated = False
    if stdout and len(stdout) > half:
        stdout = stdout[:half] + f"\n[... stdout truncated at {half} bytes ...]"
        truncated = True
    if stderr and len(stderr) > half:
        stderr = stderr[:half] + f"\n[... stderr truncated at {half} bytes ...]"
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

