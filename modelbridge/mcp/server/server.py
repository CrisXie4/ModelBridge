"""A minimal synchronous MCP server core (stdio, JSON-RPC 2.0).

Deliberately mirrors the client stack in reverse: newline-delimited JSON on
stdout, logs strictly on stderr (MCP requires stdout to carry only frames).
``handle_message`` is a pure dict→dict function so the protocol surface is
unit-testable without pipes; ``serve_stdio`` is the thin IO loop around it.

Supported methods: ``initialize`` / ``notifications/initialized`` / ``ping``
/ ``tools/list`` / ``tools/call``. Tools are plain callables registered as
:class:`ServerTool`; exceptions become ``isError`` results, never crashes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from ..protocol.capabilities import KNOWN_VERSIONS, PROTOCOL_VERSION
from ..protocol.codec import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
)

ToolFn = Callable[[dict[str, Any]], str]


@dataclass
class ServerTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn


@dataclass
class MCPServer:
    name: str = "modelbridge"
    version: str = "0"
    instructions: str = ""
    tools: dict[str, ServerTool] = field(default_factory=dict)
    initialized: bool = False

    def register(self, tool: ServerTool) -> None:
        self.tools[tool.name] = tool

    # ------------------------------------------------------------------
    # Pure protocol surface
    # ------------------------------------------------------------------
    def handle_message(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        """One inbound frame → outbound frame (or ``None`` for notifications)."""
        method = obj.get("method")
        msg_id = obj.get("id")

        if msg_id is None:  # notification — fire and forget
            if method == "notifications/initialized":
                self.initialized = True
            return None
        if not isinstance(method, str):
            # The frame parsed as JSON fine; an absent/non-string ``method``
            # is an Invalid Request (-32600), not a Parse error (-32700,
            # reserved for input that isn't valid JSON at all).
            return _error(msg_id, INVALID_REQUEST, "缺少 method 或 method 不是字符串")

        try:
            if method == "initialize":
                return _result(msg_id, self._initialize(obj.get("params") or {}))
            if method == "ping":
                return _result(msg_id, {})
            if method == "tools/list":
                return _result(msg_id, self._tools_list())
            if method == "tools/call":
                return _result(msg_id, self._tools_call(obj.get("params") or {}))
        except _InvalidParams as e:
            return _error(msg_id, INVALID_PARAMS, str(e))
        except Exception as e:
            return _error(msg_id, INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    # ------------------------------------------------------------------
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in KNOWN_VERSIONS else PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self.name, "version": self.version},
            "instructions": self.instructions,
        }

    def _tools_list(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in self.tools.values()
            ]
        }

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = self.tools.get(str(name))
        if tool is None:
            raise _InvalidParams(f"未知 tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _InvalidParams("arguments 必须是对象")
        try:
            text = tool.fn(arguments)
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ------------------------------------------------------------------
    # IO loop
    # ------------------------------------------------------------------
    def serve_stdio(self) -> int:
        # MCP frames are UTF-8; on Windows the default pipe encoding is GBK,
        # which mangles CJK payloads in both directions.
        _reconfigure_stdio()
        _log(f"{self.name} v{self.version} serving MCP on stdio "
             f"({len(self.tools)} tools)")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                _log(f"bad json: {line[:120]!r}")
                continue
            if not isinstance(obj, dict):
                continue
            reply = self.handle_message(obj)
            if reply is not None:
                sys.stdout.write(
                    json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                sys.stdout.flush()
        _log("stdin closed, exiting")
        return 0


class _InvalidParams(ValueError):
    pass


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _reconfigure_stdio() -> None:
    """Switch stdin/stdout to UTF-8 (Windows pipes default to GBK).

    Best-effort: swallows AttributeError (non-standard streams in tests /
    embedding) and ValueError (e.g. 'I/O operation on closed file' when a
    stream is redirected or already closed).
    """
    for _stream in (sys.stdin, sys.stdout):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _log(msg: str) -> None:
    print(f"[mbridge-mcp-server] {msg}", file=sys.stderr, flush=True)


__all__ = ["MCPServer", "ServerTool"]
