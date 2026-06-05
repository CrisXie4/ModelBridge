"""JSON-RPC 2.0 message data classes.

MCP rides on JSON-RPC 2.0. This module is pure data + (de)serialisation —
no IO, no protocol semantics. The transport layer turns these to/from bytes;
the session layer assigns ids and matches responses to requests.

Three message shapes on the wire:

* **Request**     — has ``id`` + ``method`` (expects a response).
* **Notification** — has ``method`` but **no** ``id`` (fire-and-forget).
* **Response**    — has ``id`` + either ``result`` or ``error``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JSONRPC_VERSION = "2.0"


@dataclass
class JsonRpcRequest:
    id: int | str
    method: str
    params: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self.id,
            "method": self.method,
        }
        if self.params is not None:
            msg["params"] = self.params
        return msg


@dataclass
class JsonRpcNotification:
    method: str
    params: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": self.method}
        if self.params is not None:
            msg["params"] = self.params
        return msg


@dataclass
class JsonRpcError:
    code: int
    message: str
    data: Any = None

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "JsonRpcError":
        return cls(
            code=int(raw.get("code", 0)),
            message=str(raw.get("message", "")),
            data=raw.get("data"),
        )


@dataclass
class JsonRpcResponse:
    id: int | str | None
    result: Any = None
    error: JsonRpcError | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> "JsonRpcResponse":
        err = raw.get("error")
        return cls(
            id=raw.get("id"),
            result=raw.get("result"),
            error=JsonRpcError.from_wire(err) if isinstance(err, dict) else None,
        )


@dataclass
class IncomingMessage:
    """A decoded inbound frame, classified by shape.

    The stdio stream is a single channel carrying responses *and* server-
    initiated traffic (notifications, and — in the future — server→client
    requests like ``sampling/createMessage``). The session reader uses
    ``kind`` to route each frame.
    """

    kind: str  # "response" | "notification" | "request"
    raw: dict[str, Any] = field(default_factory=dict)
    response: JsonRpcResponse | None = None
    notification: JsonRpcNotification | None = None
    request: JsonRpcRequest | None = None


__all__ = [
    "JSONRPC_VERSION",
    "JsonRpcRequest",
    "JsonRpcNotification",
    "JsonRpcResponse",
    "JsonRpcError",
    "IncomingMessage",
]
