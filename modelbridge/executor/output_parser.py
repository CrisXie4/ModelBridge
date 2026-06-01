"""Parse command output into structured :class:`ParsedError` records.

The goal isn't perfect coverage — it's giving downstream consumers
(today the CLI's failure summary, later the ``mbridge fix`` / ``loop``
prompt builders) a *typed handle* on what went wrong so the model can be
prompted with a tight error description instead of 8 KB of stack noise.

Four families are recognised, in priority order:

1. **Python Traceback** — ``Traceback (most recent call last):`` … final
   line ``<ErrName>: <msg>``; the *last* ``File "x", line N`` inside the
   traceback body becomes the file/line.
2. **pytest** — ``FAILED tests/foo.py::test_bar - AssertionError: …`` /
   ``E   AssertionError: …`` lines.
3. **Node.js** — ``TypeError: x is not a function`` / ``ReferenceError:
   foo is not defined`` followed by ``at thing (file:line:col)``.
4. **Generic compiler** — ``path:line:col: error: msg`` (matches
   gcc/clang/tsc/go vet).

When ``exit_code == 0`` we return an empty list — successful runs have
no errors worth parsing even if they printed warnings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .runner import CommandResult


ErrorType = Literal[
    "python_traceback",
    "pytest_failure",
    "node_error",
    "compile_error",
    "unknown",
]


@dataclass
class ParsedError:
    """One structured error extracted from command output."""

    type: ErrorType
    message: str
    file: str | None = None
    line: int | None = None
    hint: str | None = None


# --- regex bank ----------------------------------------------------------

_PY_TRACEBACK = re.compile(
    r"Traceback \(most recent call last\):\n(?P<body>(?:[ \t].*\n)+)"
    r"(?P<exc>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning|Exit)): (?P<msg>.+?)$",
    re.MULTILINE,
)
_PY_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')

_PYTEST_FAILED = re.compile(
    r"^FAILED\s+(?P<file>\S+?)(?:::(?P<test>\S+?))?(?:\s+-\s+(?P<reason>.+))?$",
    re.MULTILINE,
)

_NODE_ERR = re.compile(
    r"^(?P<exc>TypeError|ReferenceError|SyntaxError|RangeError|Error): (?P<msg>.+?)$",
    re.MULTILINE,
)
_NODE_AT = re.compile(
    r"^\s*at\s+(?:.+?\s+\()?(?P<file>[A-Za-z]:[^():\n]+|[^():\n]+\.[A-Za-z0-9]+):"
    r"(?P<line>\d+):\d+\)?",
    re.MULTILINE,
)

_COMPILE_ERR = re.compile(
    r"^(?P<file>[^\s:][^:\n]*):(?P<line>\d+):(?:\d+:)?\s*(?:error|fatal error):\s*(?P<msg>.+?)$",
    re.MULTILINE,
)


def parse_output(result: CommandResult) -> list[ParsedError]:
    """Extract structured errors from ``result``'s stdout + stderr.

    Returns an empty list when ``exit_code == 0`` — successful runs are
    not worth scanning even if they printed warning-shaped lines. On
    failure, the matcher families run in priority order and results are
    de-duplicated by ``(type, file, line, message)``.
    """
    if result.exit_code == 0 and not result.timed_out:
        return []

    text = (result.stdout or "") + "\n" + (result.stderr or "")
    seen: set[tuple[str, str | None, int | None, str]] = set()
    out: list[ParsedError] = []

    def _add(err: ParsedError) -> None:
        key = (err.type, err.file, err.line, err.message)
        if key in seen:
            return
        seen.add(key)
        out.append(err)

    for m in _PY_TRACEBACK.finditer(text):
        body = m.group("body") or ""
        frames = list(_PY_FRAME.finditer(body))
        if frames:
            last = frames[-1]
            file = last.group("file")
            line = int(last.group("line"))
        else:
            file, line = None, None
        _add(
            ParsedError(
                type="python_traceback",
                message=f"{m.group('exc')}: {m.group('msg').strip()}",
                file=file,
                line=line,
            )
        )

    for m in _PYTEST_FAILED.finditer(text):
        reason = (m.group("reason") or "").strip()
        test = m.group("test") or ""
        message = f"{test} - {reason}" if test and reason else (reason or test or "pytest failure")
        _add(
            ParsedError(
                type="pytest_failure",
                message=message,
                file=m.group("file"),
            )
        )

    # Node errors: pair each "TypeError: …" with the nearest following "at file:line"
    node_matches = list(_NODE_ERR.finditer(text))
    at_matches = list(_NODE_AT.finditer(text))
    for m in node_matches:
        file = None
        line = None
        # First "at" line after this error block.
        for a in at_matches:
            if a.start() > m.end():
                file = a.group("file")
                line = int(a.group("line"))
                break
        _add(
            ParsedError(
                type="node_error",
                message=f"{m.group('exc')}: {m.group('msg').strip()}",
                file=file,
                line=line,
            )
        )

    for m in _COMPILE_ERR.finditer(text):
        # Skip rows we already attributed to a richer parser (Python's
        # ``File "x", line N`` doesn't match this regex; pytest's
        # ``FAILED x::y`` doesn't either; this catch is mostly
        # tsc/gcc/go-style messages).
        _add(
            ParsedError(
                type="compile_error",
                message=m.group("msg").strip(),
                file=m.group("file"),
                line=int(m.group("line")),
            )
        )

    if not out and result.timed_out:
        out.append(
            ParsedError(
                type="unknown",
                message="命令超时被终止，未产生可解析的错误。",
                hint="增大 --timeout 或检查是否陷入死循环。",
            )
        )

    return out


__all__ = ["ParsedError", "ErrorType", "parse_output"]
