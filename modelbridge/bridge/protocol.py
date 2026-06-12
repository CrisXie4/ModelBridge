"""Chrome Native Messaging stdio framing + message builders.

Wire format (Chrome standard): each message is a 4-byte **native byte order**
unsigned int length prefix followed by that many bytes of UTF-8 JSON.

Two hard rules this module enforces (see project memory on Windows subprocess
quirks):

* **Binary stdio** — :func:`configure_binary_stdio` flips stdin/stdout to
  ``O_BINARY`` on Windows so CRLF translation can't corrupt the length prefix
  or the payload.
* **stdout is for frames only** — callers must never ``print`` to stdout; the
  host routes all logging to stderr / the rotating log file. This module only
  ever writes frames to the given stream.

Everything here is pure / side-effect-free except :func:`configure_binary_stdio`
(which touches the OS file descriptors) so the framing can be unit-tested with
``io.BytesIO`` without a browser.
"""

from __future__ import annotations

import json
import struct
import sys
from typing import Any, BinaryIO

# Chrome caps a message *from* the host at 1 MB. Messages *to* the host can be
# larger (up to 4 GB by spec), but we refuse anything absurd to avoid a memory
# bomb from a misbehaving / spoofed peer. 64 MB is generous for page text.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024

_LEN = struct.Struct("=I")  # native byte order, standard size, no alignment


# ---------------------------------------------------------------------------
# Message type constants
# ---------------------------------------------------------------------------

# extension / CLI client -> host
T_CHAT = "chat"
T_TOOL_RESULT = "tool_result"
T_APPROVAL_RESULT = "approval_result"
T_CANCEL = "cancel"
T_AUTH = "auth"  # CLI control-socket handshake (token), not used over stdio
T_EXEC = "exec"  # CLI control-socket: relay ONE browser tool to the extension
T_EXEC_RESULT = "exec_result"  # host -> CLI: that tool's result

# host -> extension
T_READY = "ready"
T_DELTA = "delta"
T_TOOL_CALL = "tool_call"
T_APPROVAL = "approval"
T_ASSISTANT = "assistant"
T_DONE = "done"
T_ERROR = "error"


# ---------------------------------------------------------------------------
# stdio setup
# ---------------------------------------------------------------------------

def configure_binary_stdio() -> tuple[BinaryIO, BinaryIO]:
    """Return ``(stdin_buffer, stdout_buffer)`` as raw binary streams.

    On Windows this also sets both descriptors to ``O_BINARY`` so the C
    runtime doesn't rewrite ``\\n`` <-> ``\\r\\n`` and shred our frames.
    Safe (and a no-op) on POSIX.
    """
    if sys.platform == "win32":  # pragma: no cover - platform-specific
        import msvcrt
        import os

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    return sys.stdin.buffer, sys.stdout.buffer


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

class ProtocolError(Exception):
    """Raised on a malformed frame (bad length, truncated body, bad JSON)."""


def _read_exact(stream: BinaryIO, n: int) -> bytes | None:
    """Read exactly ``n`` bytes. Returns ``None`` on clean EOF at a boundary."""
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            if not chunks:
                return None  # clean EOF before any byte of this frame
            raise ProtocolError(
                f"truncated frame: wanted {n} bytes, got {n - remaining}"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read one framed message. Returns ``None`` on clean EOF (peer closed).

    Raises :class:`ProtocolError` on a truncated / oversized / non-JSON frame.
    """
    header = _read_exact(stream, _LEN.size)
    if header is None:
        return None
    (length,) = _LEN.unpack(header)
    if length == 0:
        return {}
    if length > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message too large: {length} bytes (cap {MAX_MESSAGE_BYTES})")
    body = _read_exact(stream, length)
    if body is None:
        raise ProtocolError("EOF while reading message body")
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError(f"invalid JSON frame: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolError(f"frame is not a JSON object: {type(obj).__name__}")
    return obj


def encode_message(msg: dict[str, Any]) -> bytes:
    """Encode a message to its on-the-wire bytes (length prefix + JSON)."""
    body = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"outgoing message too large: {len(body)} bytes")
    return _LEN.pack(len(body)) + body


def write_message(stream: BinaryIO, msg: dict[str, Any]) -> None:
    """Frame and write one message, then flush. Never writes anything else."""
    stream.write(encode_message(msg))
    stream.flush()


# ---------------------------------------------------------------------------
# Message builders (host -> extension)
# ---------------------------------------------------------------------------

def ready(*, version: str, models: list[str], default_model: str | None) -> dict[str, Any]:
    return {
        "type": T_READY,
        "version": version,
        "models": models,
        "defaultModel": default_model,
    }


def delta(*, id: str, kind: str, text: str) -> dict[str, Any]:
    """A streaming chunk. ``kind`` is ``content`` or ``reasoning``."""
    return {"type": T_DELTA, "id": id, "kind": kind, "text": text}


def tool_call(*, id: str, request_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ask the extension to run a browser tool and reply with ``tool_result``."""
    return {"type": T_TOOL_CALL, "id": id, "requestId": request_id, "name": name, "args": args}


def approval(
    *, id: str, request_id: str, tool: str, summary: str, detail: str = ""
) -> dict[str, Any]:
    """Ask the side panel to confirm a mutating action."""
    return {
        "type": T_APPROVAL,
        "id": id,
        "requestId": request_id,
        "tool": tool,
        "summary": summary,
        "detail": detail,
    }


def assistant(*, id: str, content: str) -> dict[str, Any]:
    return {"type": T_ASSISTANT, "id": id, "content": content}


def done(*, id: str, stopped: str = "stop") -> dict[str, Any]:
    return {"type": T_DONE, "id": id, "stopped": stopped}


def error(*, id: str | None, message: str) -> dict[str, Any]:
    return {"type": T_ERROR, "id": id, "message": message}


__all__ = [
    "MAX_MESSAGE_BYTES",
    "ProtocolError",
    "configure_binary_stdio",
    "read_message",
    "write_message",
    "encode_message",
    # type constants
    "T_CHAT",
    "T_TOOL_RESULT",
    "T_APPROVAL_RESULT",
    "T_CANCEL",
    "T_READY",
    "T_DELTA",
    "T_TOOL_CALL",
    "T_APPROVAL",
    "T_ASSISTANT",
    "T_DONE",
    "T_ERROR",
    # builders
    "ready",
    "delta",
    "tool_call",
    "approval",
    "assistant",
    "done",
    "error",
]
