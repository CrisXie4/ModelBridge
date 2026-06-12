"""Stage 2/3 — end-to-end host round-trip with a browser tool.

Exercises the real threading path: handle_chat runs the turn, the engine calls
a browser tool which blocks on the BrowserBridge, and an inbound tool_result
(delivered via Host._route, as the main read loop would) unblocks it so the
turn can finalize.
"""

from __future__ import annotations

import io
import threading
import time

import pytest

from modelbridge.bridge.host import Host
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.schemas import ChatResponse


def _entry() -> ModelEntry:
    return ModelEntry(
        name="fake",
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="fake-1",
        base_url="http://fake.local/v1",
    )


class _ToolThenAnswerProvider:
    """First call asks for read_page; second call returns the final answer."""

    def __init__(self):
        self.n = 0

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        self.n += 1
        if self.n == 1:
            return ChatResponse(
                content="",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": "{}"},
                    }
                ],
                finish_reason="tool_calls",
            )
        return ChatResponse(content="这个页面在讲 ModelBridge。", finish_reason="stop")


@pytest.fixture
def patched(monkeypatch):
    import modelbridge.agent.loop as loop

    monkeypatch.setattr(loop, "get_provider", lambda e: _ToolThenAnswerProvider())
    monkeypatch.setattr(loop, "get_model_entry", lambda n: _entry())
    monkeypatch.setattr("modelbridge.bridge.session_runner.resolve_model_name", lambda m: "fake")


def test_browser_tool_round_trip(patched):  # noqa: ARG001
    host = Host(io.BytesIO(), io.BytesIO())
    sent: list[dict] = []
    host.send = sent.append  # capture frames instead of writing bytes

    worker = threading.Thread(
        target=host.handle_chat, args=({"type": "chat", "id": "t1", "text": "总结这个页面"},)
    )
    worker.start()

    # Wait for the engine to emit the tool_call, then deliver a tool_result
    # the way the host's read loop would.
    tc = None
    deadline = time.time() + 3
    while time.time() < deadline:
        tc = next((m for m in sent if m.get("type") == "tool_call"), None)
        if tc:
            break
        time.sleep(0.01)
    assert tc is not None, f"no tool_call emitted; got {sent}"
    assert tc["name"] == "read_page"

    host._route(
        {
            "type": "tool_result",
            "requestId": tc["requestId"],
            "ok": True,
            "content": "标题: ModelBridge\nURL: http://x\n\n正文: ...",
        }
    )

    worker.join(timeout=3)
    assert not worker.is_alive(), "turn did not finish after tool_result"

    types = [m["type"] for m in sent]
    assert "tool_call" in types
    assert sent[-2]["type"] == "assistant"
    assert "ModelBridge" in sent[-2]["content"]
    assert sent[-1]["type"] == "done"
    # pending_router is cleared after the turn
    assert host.pending_router is None
