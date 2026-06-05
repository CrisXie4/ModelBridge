"""A minimal stdio MCP server used by the MCP test-suite.

Speaks newline-delimited JSON-RPC on stdin/stdout. Implements just enough of
the protocol to exercise the client: initialize, tools (list/call), resources
(list/read), prompts (list/get). Logs go to stderr only (MCP requires stdout
to carry JSON exclusively).

Behaviour can be tweaked via argv flags:
    --no-tools / --no-resources / --no-prompts   drop that capability
    --crash-on-call                               exit during tools/call
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2025-06-18"


def _log(msg: str) -> None:
    print(f"[fake-server] {msg}", file=sys.stderr, flush=True)


def main() -> int:
    flags = set(sys.argv[1:])
    caps: dict = {}
    if "--no-tools" not in flags:
        caps["tools"] = {}
    if "--no-resources" not in flags:
        caps["resources"] = {}
    if "--no-prompts" not in flags:
        caps["prompts"] = {}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _log(f"bad json: {line!r}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        # Notifications (no id): nothing to reply.
        if msg_id is None:
            _log(f"notification {method}")
            continue

        result = _handle(method, msg.get("params") or {}, flags, caps)
        if result is _ERROR:
            _reply_error(msg_id, -32601, f"method not found: {method}")
        else:
            _reply(msg_id, result)
    return 0


_ERROR = object()


def _handle(method, params, flags, caps):
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": caps,
            "serverInfo": {"name": "fake", "version": "0.1"},
            "instructions": "fake server for tests",
        }
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo back the text argument.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            ]
        }
    if method == "tools/call":
        if "--crash-on-call" in flags:
            sys.exit(3)
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            return {"content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}],
                    "isError": False}
        return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
    if method == "resources/list":
        return {"resources": [{"uri": "mem://note", "name": "note", "mimeType": "text/plain"}]}
    if method == "resources/read":
        uri = params.get("uri")
        return {"contents": [{"uri": uri, "mimeType": "text/plain",
                              "text": f"resource body for {uri}"}]}
    if method == "prompts/list":
        return {"prompts": [{"name": "greet",
                             "description": "Greeting prompt",
                             "arguments": [{"name": "who", "required": True}]}]}
    if method == "prompts/get":
        args = params.get("arguments") or {}
        who = args.get("who", "world")
        return {"description": "Greeting prompt",
                "messages": [{"role": "user",
                              "content": {"type": "text", "text": f"Hello {who}"}}]}
    return _ERROR


def _reply(msg_id, result) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}), flush=True)


def _reply_error(msg_id, code, message) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                      "error": {"code": code, "message": message}}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
