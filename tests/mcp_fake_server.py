"""A minimal stdio MCP server used by the MCP test-suite.

Speaks newline-delimited JSON-RPC on stdin/stdout. Implements just enough of
the protocol to exercise the client: initialize, ping, tools (list/call),
resources (list/read), prompts (list/get). Logs go to stderr only (MCP
requires stdout to carry JSON exclusively).

Behaviour can be tweaked via argv flags:
    --no-tools / --no-resources / --no-prompts   drop that capability
    --crash-on-call                               exit during tools/call
    --crash-once <statefile>                      exit on the first tools/call
                                                  only (statefile marks "done")
    --notify-changed                              before answering tools/call,
                                                  emit tools/list_changed and
                                                  grow a second tool
    --sample-on-call                              during tools/call, ask the
                                                  client for a completion via
                                                  sampling/createMessage and
                                                  echo it in the tool result
"""

from __future__ import annotations

import json
import os
import sys

PROTOCOL_VERSION = "2025-06-18"

_ECHO_TOOL = {
    "name": "echo",
    "description": "Echo back the text argument.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}
_EXTRA_TOOL = {
    "name": "shout",
    "description": "Echo back the text argument, uppercased.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def _log(msg: str) -> None:
    print(f"[fake-server] {msg}", file=sys.stderr, flush=True)


def _send(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


class _State:
    def __init__(self, argv: list[str]) -> None:
        self.flags = set(a for a in argv if a.startswith("--"))
        self.crash_once_file: str | None = None
        if "--crash-once" in argv:
            self.crash_once_file = argv[argv.index("--crash-once") + 1]
        self.tools = [dict(_ECHO_TOOL)]
        self.notified = False
        self.next_server_req_id = 1000


def main() -> int:
    # MCP frames are UTF-8; on Windows the default pipe encoding is GBK.
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    state = _State(sys.argv[1:])
    caps: dict = {}
    if "--no-tools" not in state.flags:
        caps["tools"] = {"listChanged": True}
    if "--no-resources" not in state.flags:
        caps["resources"] = {}
    if "--no-prompts" not in state.flags:
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

        # Replies to server→client requests (sampling) are handled inline in
        # _handle, so a bare response frame here is unexpected — ignore it.
        if method is None:
            continue
        # Notifications (no id): nothing to reply.
        if msg_id is None:
            _log(f"notification {method}")
            continue

        result = _handle(method, msg.get("params") or {}, state, caps)
        if result is _ERROR:
            _reply_error(msg_id, -32601, f"method not found: {method}")
        else:
            _reply(msg_id, result)
    return 0


_ERROR = object()


def _request_sample(state: _State, prompt: str) -> str:
    """Server→client sampling round-trip, blocking on the client's reply."""
    req_id = state.next_server_req_id
    state.next_server_req_id += 1
    _send({
        "jsonrpc": "2.0", "id": req_id, "method": "sampling/createMessage",
        "params": {
            "messages": [
                {"role": "user", "content": {"type": "text", "text": prompt}}
            ],
            "maxTokens": 64,
        },
    })
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == req_id and "method" not in msg:
            if "error" in msg:
                return f"<sampling error: {msg['error'].get('message')}>"
            content = (msg.get("result") or {}).get("content") or {}
            return str(content.get("text") or "")
    return "<eof>"


def _handle(method, params, state: _State, caps):
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": caps,
            "serverInfo": {"name": "fake", "version": "0.1"},
            "instructions": "fake server for tests",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": state.tools}
    if method == "tools/call":
        if "--crash-on-call" in state.flags:
            sys.exit(3)
        if state.crash_once_file is not None:
            if not os.path.exists(state.crash_once_file):
                with open(state.crash_once_file, "w", encoding="utf-8") as f:
                    f.write("crashed")
                _log("crash-once: exiting")
                sys.exit(3)
        if "--notify-changed" in state.flags and not state.notified:
            state.notified = True
            state.tools.append(dict(_EXTRA_TOOL))
            _send({"jsonrpc": "2.0",
                   "method": "notifications/tools/list_changed"})
        name = params.get("name")
        args = params.get("arguments") or {}
        if "--sample-on-call" in state.flags and name == "echo":
            sampled = _request_sample(state, args.get("text", ""))
            return {"content": [{"type": "text", "text": f"sampled: {sampled}"}],
                    "isError": False}
        if name == "echo":
            return {"content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}],
                    "isError": False}
        if name == "shout":
            return {"content": [{"type": "text",
                                 "text": f"SHOUT: {str(args.get('text', '')).upper()}"}],
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
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _reply_error(msg_id, code, message) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id,
           "error": {"code": code, "message": message}})


if __name__ == "__main__":
    raise SystemExit(main())
