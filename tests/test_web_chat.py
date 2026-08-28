"""Webui chat service — end-to-end with a fake provider, no HTTP layer.

Mirrors tests/test_bridge_host.py's patching approach: swap the loop-level
provider/model entry so run_agent_turn talks to a canned responder, then
exercise:

* plain turn → user/delta?/assistant events land and the chat goes idle
* a bash tool call → approval event blocks the worker → resolve("y") unblocks
  it → the flow completes
"""

from __future__ import annotations

import time

import pytest

from modelbridge.bridge.host import Host  # noqa: F401  (imports fast enough)
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.schemas import ChatResponse
from modelbridge.web.chat_service import ChatService


def _entry() -> ModelEntry:
    return ModelEntry(
        name="fake",
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="fake-1",
        base_url="http://fake.local/v1",
    )


class _PlainProvider:
    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        return ChatResponse(content="好的，已完成。", finish_reason="stop")


class _BashThenAnswer:
    """First call asks to run_bash; second returns the final answer."""

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
                        "function": {
                            "name": "run_bash",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
        return ChatResponse(content="命令跑完了。", finish_reason="stop")


@pytest.fixture
def patched(monkeypatch):
    import modelbridge.agent.loop as loop

    monkeypatch.setattr(loop, "get_model_entry", lambda n: _entry())


def _wait_idle(svc: ChatService, cid: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = svc.snapshot(cid, after=10 ** 9)
        assert snap is not None
        if not snap["busy"]:
            return
        time.sleep(0.05)
    raise AssertionError("turn did not finish in time")


def _events_of(svc: ChatService, cid: str) -> list[dict]:
    snap = svc.snapshot(cid, after=0)
    return snap["events"] if snap else []


def test_plain_turn_streams_and_finishes(monkeypatch, tmp_path):
    import modelbridge.agent.loop as loop
    import modelbridge.web.chat_service as _cs

    monkeypatch.setattr(loop, "get_provider", lambda e: _PlainProvider())
    monkeypatch.setattr(loop, "get_model_entry", lambda n: _entry())
    monkeypatch.setattr(_cs, "resolve_model_name", lambda m: "fake")
    svc = ChatService(cwd=tmp_path)
    created = svc.create()
    out = svc.submit(created["id"], "你好", None, "fake")
    assert out["ok"] is True
    _wait_idle(svc, created["id"])

    types = [e["type"] for e in _events_of(svc, created["id"])]
    assert types[0] == "user"
    assert "assistant" in types and types[-1] == "done"
    assistant = next(e for e in _events_of(svc, created["id"]) if e["type"] == "assistant")
    assert "完成" in assistant["content"]
    # 注：fake provider 不产生流式 chunk，delta 事件为空是正常的。


def test_bash_tool_blocks_on_approval_until_resolved(monkeypatch, tmp_path):
    import modelbridge.agent.loop as loop
    import modelbridge.web.chat_service as _cs

    monkeypatch.setattr(loop, "get_provider", lambda e: _BashThenAnswer())
    monkeypatch.setattr(loop, "get_model_entry", lambda n: _entry())
    monkeypatch.setattr(_cs, "resolve_model_name", lambda m: "fake")
    svc = ChatService(cwd=tmp_path)
    created = svc.create()
    svc.submit(created["id"], "跑一下 echo", None, "fake")

    # 等审批事件出现（worker 阻塞在 done.wait()）。
    deadline = time.time() + 5
    aid = ""
    while time.time() < deadline:
        approvals = [e for e in _events_of(svc, created["id"]) if e["type"] == "approval"]
        if approvals:
            aid = approvals[0]["approval_id"]
            break
        time.sleep(0.05)
    assert aid, "approval event never appeared"

    assert svc.resolve_approval(aid, "y") is True
    assert svc.resolve_approval(aid, "n") is False  # 二次处理被拒
    _wait_idle(svc, created["id"])

    kinds = [e["type"] for e in _events_of(svc, created["id"])]
    assert "tool" in kinds and "decision" in kinds and "assistant" in kinds


def test_submit_rejects_when_busy_and_missing_chat(tmp_path):
    svc = ChatService(cwd=tmp_path)
    out = svc.submit("ghost", "hi", None, "fake")
    assert out["ok"] is False
