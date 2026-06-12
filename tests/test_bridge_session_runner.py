"""Stage 1 — SessionRunner turns model output into the right frame sequence.

Uses a fake streaming provider so no network / API key is needed.
"""

from __future__ import annotations

import pytest

from modelbridge.bridge.session_runner import SessionRunner
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.providers.base import StreamEvent
from modelbridge.schemas import ChatResponse, ProviderError


class _FakeHost:
    """Minimal stand-in for bridge.host.Host: a send sink + pending_router slot."""

    def __init__(self, send):
        self.send = send
        self.pending_router = None


class _FakeProvider:
    """Streams two content chunks then a final assembled response."""

    name = "fake"

    def __init__(self, chunks=("你好", "，世界"), final="你好，世界"):
        self._chunks = chunks
        self._final = final

    def stream_chat(self, request, *, timeout):  # noqa: ARG002
        for c in self._chunks:
            yield StreamEvent(kind="content", text=c)
        yield StreamEvent(kind="done", response=ChatResponse(content=self._final))


class _ErrorProvider:
    name = "fake-err"

    def stream_chat(self, request, *, timeout):  # noqa: ARG002
        raise ProviderError("rate limited", provider="fake-err", status_code=429, hint="稍后再试")
        yield  # pragma: no cover - generator marker


def _entry() -> ModelEntry:
    return ModelEntry(
        name="fake",
        provider=ProviderType.OPENAI_COMPATIBLE,
        model="fake-1",
        base_url="http://fake.local/v1",
    )


@pytest.fixture
def patched(monkeypatch):
    """Patch model resolution + provider lookup in both the runner and loop."""
    monkeypatch.setattr(
        "modelbridge.bridge.session_runner.resolve_model_name", lambda m: "fake"
    )
    monkeypatch.setattr("modelbridge.agent.loop.get_model_entry", lambda name: _entry())


def test_chat_emits_delta_then_assistant_then_done(monkeypatch, patched):
    import modelbridge.agent.loop as loop

    monkeypatch.setattr(loop, "get_provider", lambda entry: _FakeProvider())
    frames: list[dict] = []
    runner = SessionRunner(_FakeHost(frames.append))

    runner.run({"type": "chat", "id": "t1", "text": "hi"})

    kinds = [f["type"] for f in frames]
    assert kinds == ["delta", "delta", "assistant", "done"]
    assert frames[0] == {"type": "delta", "id": "t1", "kind": "content", "text": "你好"}
    assert frames[2] == {"type": "assistant", "id": "t1", "content": "你好，世界"}
    assert frames[3]["stopped"] == "stop"


def test_session_persists_across_turns(monkeypatch, patched):
    import modelbridge.agent.loop as loop

    monkeypatch.setattr(loop, "get_provider", lambda entry: _FakeProvider())
    runner = SessionRunner(_FakeHost(lambda f: None))

    runner.run({"type": "chat", "id": "t1", "text": "first"})
    runner.run({"type": "chat", "id": "t2", "text": "second"})

    roles = [m.role for m in runner.session.messages]
    # system, user(first), assistant, user(second), assistant
    assert roles == ["system", "user", "assistant", "user", "assistant"]


def test_provider_error_emits_error_and_done(monkeypatch, patched):
    import modelbridge.agent.loop as loop

    monkeypatch.setattr(loop, "get_provider", lambda entry: _ErrorProvider())
    frames: list[dict] = []
    runner = SessionRunner(_FakeHost(frames.append))

    runner.run({"type": "chat", "id": "t9", "text": "boom"})

    assert frames[-2]["type"] == "error"
    assert "rate limited" in frames[-2]["message"]
    assert "429" in frames[-2]["message"]
    assert frames[-1] == {"type": "done", "id": "t9", "stopped": "provider_error"}


def test_approve_always_persists_across_turns():
    """ALWAYS chosen in one turn must auto-approve the same tool in later turns.

    Regression: a fresh AgentContext is built per message, so the
    'approve always' memory must live on the runner, not the per-turn ctx.
    """
    runner = SessionRunner(_FakeHost(lambda f: None))

    class _Bridge:
        def __init__(self):
            self.asked = 0

        def request_approval(self, *, tool, summary, detail=""):
            self.asked += 1
            return "always"

        def call(self, name, args, *, timeout=None):
            return {"ok": True, "content": ""}

    bridge = _Bridge()
    ctx1 = runner._build_context(bridge)
    assert ctx1.confirm(tool="click", summary="x") is True  # asks, remembers
    ctx2 = runner._build_context(bridge)  # new turn, new ctx
    assert ctx2.confirm(tool="click", summary="x") is True  # should NOT ask again
    assert bridge.asked == 1


def test_unresolved_model_emits_error(monkeypatch):
    from modelbridge.client import ChatError

    def _raise(_m):
        raise ChatError("no default model")

    monkeypatch.setattr("modelbridge.bridge.session_runner.resolve_model_name", _raise)
    frames: list[dict] = []
    SessionRunner(_FakeHost(frames.append)).run({"type": "chat", "id": "t0", "text": "x"})

    assert frames[0]["type"] == "error" and "no default model" in frames[0]["message"]
    assert frames[1] == {"type": "done", "id": "t0", "stopped": "error"}
