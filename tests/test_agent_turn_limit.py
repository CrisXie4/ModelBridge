"""``max_iters`` is an opt-in runaway guard, not a default cap.

Regression for: the default was 20 iterations per turn, which cut real
coding tasks in half with ``[到达单轮 tool_call 上限 (20)]``. The default
is now unlimited (``None``); an explicit ``--max-iters`` still caps the
loop and reports ``stopped_reason="max_iters"``.

The fake providers below call an unregistered tool name on purpose —
``ToolRegistry.dispatch`` answers unknown names with an error ToolResult,
which keeps the loop going without defining any real tool here.
"""

from __future__ import annotations

from pathlib import Path

import modelbridge.agent.loop as loop
from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.loop import run_agent_turn
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.session import Session
from modelbridge.agent.tools import ToolRegistry
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.schemas import ChatResponse


def _call_payload(i: int) -> dict:
    return {
        "id": "c%d" % i,
        "type": "function",
        "function": {"name": "ping", "arguments": "{}"},
    }


class _AlwaysCallsProvider:
    """Every response asks for one more tool call — a runaway model."""

    n = 0

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        _AlwaysCallsProvider.n += 1
        return ChatResponse(
            content="",
            tool_calls=[_call_payload(_AlwaysCallsProvider.n)],
            finish_reason="tool_calls",
        )


class _StopsAfterProvider:
    """Calls a tool N times, then answers in plain text."""

    def __init__(self, tool_turns: int = 25):
        self.seen = 0
        self.tool_turns = tool_turns

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        self.seen += 1
        if self.seen <= self.tool_turns:
            return ChatResponse(
                content="",
                tool_calls=[_call_payload(self.seen)],
                finish_reason="tool_calls",
            )
        return ChatResponse(content="done", finish_reason="stop")


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
    ctx = AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes)
    session = Session(model_name="fake")
    session.add_user("go")
    return session, ctx, ToolRegistry()


def test_default_is_unlimited(monkeypatch):
    """25 tool iterations (> the old cap of 20) finish normally by default."""
    provider = _StopsAfterProvider(tool_turns=25)
    session, ctx, registry = _setup(monkeypatch, provider)
    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake"
    )
    assert result.stopped_reason == "stop"
    assert result.iterations == 26
    assert len(result.tool_calls_executed) == 25


def test_explicit_cap_stops_and_reports(monkeypatch):
    provider = _AlwaysCallsProvider()
    session, ctx, registry = _setup(monkeypatch, provider)
    result = run_agent_turn(
        session=session, ctx=ctx, registry=registry, model_name="fake",
        max_iters=3,
    )
    assert result.stopped_reason == "max_iters"
    assert result.iterations == 3
    assert len(result.tool_calls_executed) == 3
