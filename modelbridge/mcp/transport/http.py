"""Streamable HTTP transport (M4): talk to a remote MCP server over HTTP.

Implements the client side of the MCP "Streamable HTTP" transport:

* Every outbound JSON-RPC message is an HTTP ``POST`` to the endpoint URL.
* The server answers either ``application/json`` (one message, or a batch
  array) or ``text/event-stream`` (an SSE stream of messages — used when the
  server wants to interleave notifications / server→client requests before
  the final response).
* ``Mcp-Session-Id`` returned on the ``initialize`` response is echoed on
  every subsequent request; ``DELETE`` with that id on close ends the session.
* After a session id is known we open a best-effort ``GET`` SSE stream for
  server-initiated traffic (sampling requests, list_changed notifications).
  Servers that don't support it return 4xx/405 and we simply go without.

Like the stdio transport, inbound messages land in a queue that ``receive``
pulls from — the session layer sees the exact same blocking interface.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Iterator

import httpx

from ..errors import MCPConnectError, MCPTimeoutError, MCPTransportError
from ..logging import log_lifecycle
from ..protocol.capabilities import PROTOCOL_VERSION
from ..protocol.codec import classify_message
from ..protocol.messages import IncomingMessage
from .base import Transport

_EOF = object()  # sentinel: the channel is permanently gone
_SESSION_HEADER = "mcp-session-id"


class HttpTransport(Transport):
    def __init__(
        self,
        *,
        server_id: str,
        url: str,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 20.0,
    ) -> None:
        self.server_id = server_id
        self._url = url
        self._extra_headers = dict(headers or {})
        self._connect_timeout = connect_timeout
        self._client: httpx.Client | None = None
        self._inbox: "queue.Queue[Any]" = queue.Queue()
        self._session_id: str | None = None
        self._closed = False
        self._listener_started = False
        self._lock = threading.Lock()  # guards session_id / listener start

    # ------------------------------------------------------------------
    def start(self, *, timeout: float) -> None:
        if not self._url.lower().startswith(("http://", "https://")):
            raise MCPConnectError(
                f"非法 MCP server url: {self._url}",
                server_id=self.server_id,
                hint="url 必须以 http:// 或 https:// 开头",
            )
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, self._connect_timeout)),
            follow_redirects=True,
        )
        log_lifecycle(self.server_id, "http_open", self._url)

    # ------------------------------------------------------------------
    def _headers(self, *, accept_stream: bool) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream" if accept_stream
            else "application/json",
            # Required by the 2025-06-18 revision on every post-initialize request.
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            h[_SESSION_HEADER] = self._session_id
        h.update(self._extra_headers)
        return h

    # ------------------------------------------------------------------
    def send(self, frame: dict[str, Any]) -> None:
        if self._client is None:
            raise MCPTransportError("transport 未启动", server_id=self.server_id)
        if self._closed:
            raise MCPTransportError("transport 已关闭", server_id=self.server_id)

        try:
            # Stream the response: it may be JSON (read fully here) or SSE
            # (handed to a reader thread so `send` returns immediately).
            req = self._client.build_request(
                "POST", self._url, content=json.dumps(frame, ensure_ascii=False),
                headers=self._headers(accept_stream=True),
            )
            resp = self._client.send(req, stream=True)
        except httpx.ConnectError as e:
            raise MCPConnectError(
                f"无法连接 MCP server: {self._url}",
                server_id=self.server_id,
                hint="检查 url 是否可达、server 是否在运行",
                raw=str(e),
            ) from e
        except httpx.TimeoutException as e:
            raise MCPTimeoutError(
                f"连接 MCP server 超时: {self._url}",
                server_id=self.server_id, raw=str(e),
            ) from e
        except httpx.HTTPError as e:
            raise MCPTransportError(
                f"HTTP 请求失败: {e}", server_id=self.server_id, raw=str(e)
            ) from e

        self._capture_session_id(resp)

        if resp.status_code in (401, 403):
            resp.close()
            raise MCPConnectError(
                f"MCP server 鉴权失败 (HTTP {resp.status_code})",
                server_id=self.server_id,
                hint="在该 server 配置的 headers 里加上 Authorization 等凭证",
            )
        if resp.status_code == 404 and self._session_id:
            resp.close()
            raise MCPTransportError(
                "MCP session 已过期 (HTTP 404)",
                server_id=self.server_id,
                hint="session 被 server 回收；重连后会重新握手",
            )
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.read().decode("utf-8", errors="replace")[:300]
            except httpx.HTTPError:
                pass
            resp.close()
            raise MCPTransportError(
                f"MCP server 返回 HTTP {resp.status_code}",
                server_id=self.server_id, raw=body,
            )

        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if resp.status_code == 202 or ctype == "":
            # Accepted (notification / response from us) — no body expected.
            resp.close()
            return
        if ctype == "text/event-stream":
            t = threading.Thread(target=self._pump_sse, args=(resp,), daemon=True)
            t.start()
        else:
            try:
                data = json.loads(resp.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise MCPTransportError(
                    f"MCP server 返回非 JSON 响应: {e}",
                    server_id=self.server_id,
                ) from e
            finally:
                resp.close()
            self._enqueue(data)

        self._maybe_start_listener()

    # ------------------------------------------------------------------
    def _capture_session_id(self, resp: httpx.Response) -> None:
        sid = resp.headers.get(_SESSION_HEADER)
        if sid and sid != self._session_id:
            with self._lock:
                self._session_id = sid
            log_lifecycle(self.server_id, "http_session", sid[:16])

    def _enqueue(self, data: Any) -> None:
        # A JSON body may be a single message or a batch array.
        if isinstance(data, list):
            for item in data:
                self._inbox.put(item)
        else:
            self._inbox.put(data)

    # ------------------------------------------------------------------
    def _pump_sse(self, resp: httpx.Response) -> None:
        """Drain one SSE response stream into the inbox. Runs on a thread."""
        try:
            for data in _iter_sse_data(resp.iter_lines()):
                try:
                    self._enqueue(json.loads(data))
                except json.JSONDecodeError:
                    log_lifecycle(self.server_id, "sse_bad_json", data[:120])
        except httpx.HTTPError:
            pass  # stream broke; the pending receive() will time out and report
        finally:
            try:
                resp.close()
            except httpx.HTTPError:
                pass

    # ------------------------------------------------------------------
    def _maybe_start_listener(self) -> None:
        """Open the standalone GET SSE stream once we have a session id."""
        with self._lock:
            if self._listener_started or self._session_id is None or self._closed:
                return
            self._listener_started = True
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self) -> None:
        assert self._client is not None
        headers = self._headers(accept_stream=True)
        headers["Accept"] = "text/event-stream"
        try:
            with self._client.stream(
                "GET", self._url, headers=headers, timeout=None
            ) as resp:
                if resp.status_code != 200:
                    return  # server doesn't offer a listen stream — fine
                log_lifecycle(self.server_id, "http_listen", "GET stream open")
                for data in _iter_sse_data(resp.iter_lines()):
                    try:
                        self._enqueue(json.loads(data))
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPError:
            return

    # ------------------------------------------------------------------
    def receive(self, *, timeout: float) -> IncomingMessage:
        try:
            item = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise MCPTimeoutError(
                f"等待 server 响应超时（{timeout:.0f}s）",
                server_id=self.server_id,
                hint="server 可能卡住；可调大 request_timeout 或检查网络",
            ) from None
        if item is _EOF:
            raise MCPTransportError("HTTP transport 已关闭", server_id=self.server_id)
        return classify_message(item)

    # ------------------------------------------------------------------
    def is_alive(self) -> bool:
        return self._client is not None and not self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        client = self._client
        if client is None:
            return
        if self._session_id:
            try:  # politely end the server-side session
                client.delete(
                    self._url,
                    headers=self._headers(accept_stream=False),
                    timeout=5.0,
                )
            except httpx.HTTPError:
                pass
        try:
            client.close()
        except httpx.HTTPError:
            pass
        self._inbox.put(_EOF)
        log_lifecycle(self.server_id, "closed")


def _iter_sse_data(lines: Iterator[str]) -> Iterator[str]:
    """Yield the ``data:`` payload of each SSE event (event/id/retry ignored)."""
    buf: list[str] = []
    for line in lines:
        if line == "":
            if buf:
                yield "\n".join(buf)
                buf = []
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
    if buf:
        yield "\n".join(buf)


__all__ = ["HttpTransport"]
