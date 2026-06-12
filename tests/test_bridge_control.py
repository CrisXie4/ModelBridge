"""CLI control channel — the host relays single browser-tool execs.

The agent now runs in the `mbridge` process; the control socket only relays one
DOM action at a time to the extension. Two layers are tested without a browser:

* ``Host.relay_exec`` — sends a tool_call to (mock) stdout and resolves when a
  tool_result is delivered (as the extension would over stdin).
* ``RemoteBrowserBridge`` <-> ``ControlServer`` over a real loopback socket,
  with ``relay_exec`` stubbed (no extension needed).
"""

from __future__ import annotations

import io
import threading
import time

import pytest

from modelbridge.bridge.control import (
    RemoteBrowserBridge,
    endpoint_path,
    set_control,
)
from modelbridge.bridge.host import Host


# ---------------------------------------------------------------------------
# Host.relay_exec — the extension side is simulated via pending_router.deliver
# ---------------------------------------------------------------------------

def test_relay_exec_round_trip():
    host = Host(io.BytesIO(), io.BytesIO())
    sent: list[dict] = []
    host.send = sent.append  # capture tool_call frames

    result: dict = {}

    def run():
        result["res"] = host.relay_exec("read_page", {"max_chars": 100})

    worker = threading.Thread(target=run)
    worker.start()

    tc = None
    deadline = time.time() + 3
    while time.time() < deadline:
        tc = next((m for m in sent if m.get("type") == "tool_call"), None)
        if tc:
            break
        time.sleep(0.01)
    assert tc is not None and tc["name"] == "read_page"
    assert tc["args"] == {"max_chars": 100}

    host.pending_router.deliver(
        {"type": "tool_result", "requestId": tc["requestId"], "ok": True, "content": "页面正文"}
    )
    worker.join(timeout=3)

    assert result["res"] == {"ok": True, "content": "页面正文"}
    assert host.pending_router is None  # cleared after the relay


# ---------------------------------------------------------------------------
# RemoteBrowserBridge <-> ControlServer over a real socket (relay_exec stubbed)
# ---------------------------------------------------------------------------

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    set_control(enabled=True, token="tok")

    host = Host(io.BytesIO(), io.BytesIO())
    # Stub the relay so the test doesn't need the extension / stdin loop.
    host.relay_exec = lambda name, args, timeout=None: {  # type: ignore[method-assign]
        "ok": True,
        "content": f"ran {name}:{args.get('selector', '')}",
    }
    host._start_control_server()

    deadline = time.time() + 3
    while time.time() < deadline and not endpoint_path().exists():
        time.sleep(0.02)
    assert endpoint_path().exists(), "control endpoint not published"
    yield host
    if host._control is not None:
        host._control.stop()


def test_remote_bridge_relays_over_socket(server):
    bridge = RemoteBrowserBridge()
    res = bridge.call("click", {"selector": "a.btn"})
    assert res == {"ok": True, "content": "ran click:a.btn"}
    # connection is reused for a second call
    res2 = bridge.call("read_page", {})
    assert res2["ok"] is True
    bridge.close()


def test_read_timeout_exceeds_host_tool_timeout(server):
    """Regression: a slow page load (navigate waits ~60s, host caps at the tool
    timeout) must NOT be cut off by the client socket read timeout."""
    from modelbridge.bridge.browser_bridge import DEFAULT_TOOL_TIMEOUT

    bridge = RemoteBrowserBridge()
    bridge.call("read_page", {})  # establishes the connection
    assert bridge._conn is not None
    assert bridge._conn.gettimeout() > DEFAULT_TOOL_TIMEOUT
    bridge.close()


def test_remote_bridge_disabled_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))  # control off by default
    res = RemoteBrowserBridge().call("read_page", {})
    assert res["ok"] is False
    assert "未启用" in res["content"]


def test_remote_bridge_available_precheck(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    ok, _reason = RemoteBrowserBridge().available()
    assert ok is False  # disabled by default


def test_disabled_host_does_not_start_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    host = Host(io.BytesIO(), io.BytesIO())
    host._start_control_server()
    assert host._control is None
    assert not endpoint_path().exists()


def test_endpoint_does_not_leak_token(server):
    import json

    data = json.loads(endpoint_path().read_text(encoding="utf-8"))
    assert "token" not in data  # token lives in the control config, not here
    assert "port" in data
