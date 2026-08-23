"""Seamless compaction inside the agent loop — overflow recovery.

The loop's contract (see :func:`modelbridge.agent.loop.run_agent_turn`):

* A provider error that means "request exceeded the context window"
  triggers ONE compact-then-retry before the error surfaces.
* The retry is bounded: a second overflow error returns
  ``stopped_reason="provider_error"`` with the original error.
* Non-overflow provider errors never trigger the compaction retry.

The fake providers + monkeypatched ``maybe_auto_compact`` keep this
network-free; compaction internals have their own suite in
``test_compact.py``.
"""

from __future__ import annotations

from pathlib import Path


import modelbridge.agent.compact as compact
import modelbridge.agent.loop as loop
from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.loop import run_agent_turn
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.session import Session
from modelbridge.agent.tools import ToolRegistry
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.schemas import ChatResponse, ProviderError


_OVERFLOW_MSG = (
    "This model's maximum context length is 65536 tokens. "
    "However, your messages resulted in 100000 tokens"
)


class _OverflowOnceProvider:
    """Raises a context-overflow error once, then answers normally."""

    def __init__(self):
        self.calls = 0

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            raise ProviderError(_OVERFLOW_MSG, provider="fake", status_code=400)
        return ChatResponse(content="recovered", finish_reason="stop")


class _AlwaysOverflowProvider:
    """Every call overflows — the retry must stay bounded."""

    def __init__(self):
        self.calls = 0

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        self.calls += 1
        raise ProviderError(_OVERFLOW_MSG, provider="fake", status_code=400)


class _AuthFailProvider:
    """Unrelated 401 — must NOT trigger a compaction retry."""

    def __init__(self):
        self.calls = 0

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        self.calls += 1
        raise ProviderError("401 unauthorized", provider="fake", status_code=401)


def _setup(monkeypatch, provider):
    monkeypatch.setattr(loop, "get_provider", lambda e: provider)
    monkeypatch.setattr(
        loop,
        "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE,
            model="f", base_url="http://x/v1",
        ),
    )
    calls: list[dict] = []
    notices: list[str] = []

    def fake_compact(session, *, model_name, on_system=None, force=False):
        calls.append({"force": force})
        return None

    monkeypatch.setattr(compact, "maybe_auto_compact", fake_compact)
    ctx = AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes)
    session = Session(model_name="fake")
    session.add_user("go")
    return session, ctx, ToolRegistry(), calls, notices


def test_overflow_error_compacts_and_retries_once(monkeypatch):
    provider = _OverflowOnceProvider()
    session, ctx, registry, calls, notices = _setup(monkeypatch, provider)
    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        on_system=notices.append,
    )
    assert result.stopped_reason == "stop"
    assert result.final_response.content == "recovered"
    # One forced compaction for the recovery, plus the per-iteration
    # pressure checks (non-forced) that found nothing to do.
    assert {"force": True} in calls
    assert provider.calls == 2
    assert any("上下文超限" in n for n in notices)


def test_overflow_retry_is_bounded(monkeypatch):
    provider = _AlwaysOverflowProvider()
    session, ctx, registry, calls, _ = _setup(monkeypatch, provider)
    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake"
    )
    assert result.stopped_reason == "provider_error"
    assert result.error is not None and "context length" in result.error.message
    # First call + exactly one retry — no retry storm.
    assert provider.calls == 2
    assert calls.count({"force": True}) == 1


def test_unrelated_provider_error_skips_recovery(monkeypatch):
    provider = _AuthFailProvider()
    session, ctx, registry, calls, _ = _setup(monkeypatch, provider)
    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake"
    )
    assert result.stopped_reason == "provider_error"
    assert provider.calls == 1
    assert {"force": True} not in calls
