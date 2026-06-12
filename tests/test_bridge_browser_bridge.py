"""Stage 2 — BrowserBridge correlates tool_call frames with tool_result frames.

The bridge blocks the worker thread until a result is delivered (simulating the
host's main thread), so each test delivers from a helper thread.
"""

from __future__ import annotations

import threading
import time

from modelbridge.bridge.browser_bridge import BrowserBridge


def _collect_sender():
    sent: list[dict] = []
    return sent, sent.append


def test_call_sends_tool_call_and_resolves_on_result():
    sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")

    # Deliver the result shortly after the call blocks.
    def responder():
        time.sleep(0.05)
        # Echo back the requestId the bridge generated.
        rid = sent[0]["requestId"]
        bridge.deliver({"type": "tool_result", "requestId": rid, "ok": True, "content": "页面正文"})

    threading.Thread(target=responder, daemon=True).start()
    res = bridge.call("read_page", {"max_chars": 100}, timeout=2.0)

    assert res == {"ok": True, "content": "页面正文"}
    assert sent[0]["type"] == "tool_call"
    assert sent[0]["name"] == "read_page"
    assert sent[0]["args"] == {"max_chars": 100}
    assert sent[0]["id"] == "t1"


def test_call_times_out_when_no_result():
    _sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")
    res = bridge.call("read_page", {}, timeout=0.1)
    assert res["ok"] is False
    assert "超时" in res["content"]


def test_failed_result_maps_to_error():
    sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")

    def responder():
        time.sleep(0.02)
        rid = sent[0]["requestId"]
        bridge.deliver({"type": "tool_result", "requestId": rid, "ok": False, "content": "无法注入"})

    threading.Thread(target=responder, daemon=True).start()
    res = bridge.call("click", {"selector": "a"}, timeout=2.0)
    assert res == {"ok": False, "content": "无法注入"}


def test_cancel_unblocks_pending_call():
    _sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")

    def canceller():
        time.sleep(0.05)
        bridge.cancel()

    threading.Thread(target=canceller, daemon=True).start()
    res = bridge.call("read_page", {}, timeout=2.0)
    assert res["ok"] is False
    assert "取消" in res["content"]


def test_deliver_for_unknown_request_is_ignored():
    _sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")
    # Should not raise even though nothing is waiting.
    bridge.deliver({"type": "tool_result", "requestId": "nope", "ok": True, "content": "x"})


def test_request_approval_returns_decision():
    sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")

    def responder():
        time.sleep(0.02)
        rid = sent[0]["requestId"]
        bridge.deliver({"type": "approval_result", "requestId": rid, "decision": "always"})

    threading.Thread(target=responder, daemon=True).start()
    decision = bridge.request_approval(tool="click", summary="点击", detail="a", timeout=2.0)
    assert decision == "always"
    assert sent[0]["type"] == "approval"


def test_request_approval_timeout_denies():
    _sent, send = _collect_sender()
    bridge = BrowserBridge(send, turn_id="t1")
    assert bridge.request_approval(tool="click", summary="点击", timeout=0.1) == "no"


def test_split_sinks_route_tool_and_approval_separately():
    """tool_call goes to the tool sink (extension); approval to its own sink (CLI)."""
    tool_frames: list[dict] = []
    approval_frames: list[dict] = []
    bridge = BrowserBridge(tool_frames.append, approval_send=approval_frames.append, turn_id="t1")

    # tool call times out fast (no responder) — we only care which sink it hit.
    bridge.call("read_page", {}, timeout=0.05)
    bridge.request_approval(tool="click", summary="点击", timeout=0.05)

    assert len(tool_frames) == 1 and tool_frames[0]["type"] == "tool_call"
    assert len(approval_frames) == 1 and approval_frames[0]["type"] == "approval"
