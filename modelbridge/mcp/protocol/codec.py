"""Encode/decode JSON-RPC frames + map JSON-RPC errors to MCP exceptions.

MCP stdio frames are newline-delimited JSON: exactly one JSON object per line,
no embedded newlines. ``encode_line`` guarantees that invariant;
``decode_message`` classifies an inbound object into request / response /
notification.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import MCPProtocolError
from .messages import (
    IncomingMessage,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
)

# JSON-RPC standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def encode_line(message: dict[str, Any]) -> str:
    """Serialise one frame to a single newline-terminated line."""
    # ``ensure_ascii=False`` keeps CJK payloads compact; no embedded newline
    # because we don't pretty-print.
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_message(line: str) -> IncomingMessage:
    """Parse one inbound line and classify it.

    Raises :class:`MCPProtocolError` on malformed JSON or a non-object frame.
    """
    line = line.strip()
    if not line:
        raise MCPProtocolError("收到空帧", hint="server 可能崩溃或输出了非 JSON 内容")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise MCPProtocolError(
            f"无法解析 JSON-RPC 帧: {e}",
            hint="server 可能把日志写到了 stdout；MCP 要求 stdout 只发 JSON，日志走 stderr",
            raw=line[:500],
        ) from e
    return classify_message(obj)


def classify_message(obj: Any) -> IncomingMessage:
    """Classify an already-parsed JSON-RPC object (used by the HTTP transport,
    where the body arrives pre-decoded from JSON / SSE events)."""
    if not isinstance(obj, dict):
        raise MCPProtocolError(f"JSON-RPC 帧不是对象: {type(obj).__name__}", raw=obj)

    has_id = "id" in obj and obj["id"] is not None
    has_method = "method" in obj

    if has_method and has_id:
        return IncomingMessage(
            kind="request",
            raw=obj,
            request=JsonRpcRequest(
                id=obj["id"], method=str(obj["method"]), params=obj.get("params")
            ),
        )
    if has_method and not has_id:
        return IncomingMessage(
            kind="notification",
            raw=obj,
            notification=JsonRpcNotification(
                method=str(obj["method"]), params=obj.get("params")
            ),
        )
    # No method → it's a response (result or error), keyed by id.
    return IncomingMessage(kind="response", raw=obj, response=JsonRpcResponse.from_wire(obj))


__all__ = [
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "encode_line",
    "decode_message",
    "classify_message",
]
