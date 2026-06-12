"""Ctrl-C during tool dispatch must leave the session history legal.

Regression for: assistant message with tool_calls already in the session, user
interrupts the (slow) tool, no tool message gets appended → next request fails
with DeepSeek 400 "tool_calls must be followed by tool messages".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.loop import run_agent_turn
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.session import Session
from modelbridge.agent.tools import ToolRegistry
from modelbridge.agent.tools.base import Tool, ToolResult
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.schemas import ChatResponse


class _InterruptedTool(Tool):
    name = "slow_tool"
    description = "simulates the user pressing Ctrl-C mid-execution"

    def json_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, args, ctx):  # noqa: ARG002
        raise KeyboardInterrupt


class _OkTool(Tool):
    name = "ok_tool"
    description = "completes normally"

    def json_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, args, ctx):  # noqa: ARG002
        return ToolResult(content="done")


class _Provider:
    """First (only) call: ask for ok_tool then slow_tool in one message."""

    def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
        return ChatResponse(
            content="",
            tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "ok_tool", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "slow_tool", "arguments": "{}"}},
            ],
            finish_reason="tool_calls",
        )


def test_interrupt_backfills_tool_results(monkeypatch):
    import modelbridge.agent.loop as loop

    monkeypatch.setattr(loop, "get_provider", lambda e: _Provider())
    monkeypatch.setattr(
        loop,
        "get_model_entry",
        lambda n: ModelEntry(
            name="fake", provider=ProviderType.OPENAI_COMPATIBLE, model="f", base_url="http://x/v1"
        ),
    )

    registry = ToolRegistry()
    registry.register(_OkTool())
    registry.register(_InterruptedTool())
    ctx = AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes)
    session = Session(model_name="fake")
    session.add_user("do it")

    with pytest.raises(KeyboardInterrupt):
        run_agent_turn(
            session=session, ctx=ctx, registry=registry, model_name="fake"
        )

    # Every tool_call_id in the assistant message has a matching tool message.
    assistant = next(m for m in session.messages if m.role == "assistant")
    want_ids = {tc["id"] for tc in assistant.tool_calls}
    got_ids = {m.tool_call_id for m in session.messages if m.role == "tool"}
    assert want_ids == got_ids == {"c1", "c2"}

    # The interrupted one carries the synthetic note; the completed one its result.
    by_id = {m.tool_call_id: m.content for m in session.messages if m.role == "tool"}
    assert by_id["c1"] == "done"
    assert "中断" in by_id["c2"]
