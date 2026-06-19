from modelbridge.agent.tools.base import ToolResult
from modelbridge.schemas import ChatMessage


def test_toolresult_has_extra_messages_default_none():
    r = ToolResult(content="ok")
    assert r.extra_messages is None


def test_toolresult_carries_extra_messages():
    m = ChatMessage(role="user", content=[{"type": "text", "text": "img"}])
    r = ToolResult(content="loaded", extra_messages=[m])
    assert r.extra_messages == [m]


def test_loop_appends_extra_messages(monkeypatch):
    """A tool returning extra_messages → those messages land in the session."""
    from pathlib import Path

    import modelbridge.agent.loop as loop
    from modelbridge.agent.context import AgentContext, auto_yes
    from modelbridge.agent.security import PathPolicy
    from modelbridge.agent.session import Session
    from modelbridge.agent.tools import ToolRegistry
    from modelbridge.agent.tools.base import Tool
    from modelbridge.models import ModelEntry, ProviderType
    from modelbridge.schemas import ChatResponse

    class _InjectTool(Tool):
        name = "inject"
        description = "returns an extra user message"

        def json_schema(self):
            return {"type": "object", "properties": {}}

        def execute(self, args, ctx):  # noqa: ARG002
            extra = ChatMessage(role="user", content=[{"type": "text", "text": "[injected]"}])
            return ToolResult(content="done", extra_messages=[extra])

    calls = {"n": 0}

    class _P:
        def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
            calls["n"] += 1
            if calls["n"] == 1:
                return ChatResponse(
                    content="",
                    tool_calls=[{"id": "c1", "type": "function",
                                 "function": {"name": "inject", "arguments": "{}"}}],
                    finish_reason="tool_calls",
                )
            return ChatResponse(content="final")

    monkeypatch.setattr(loop, "get_provider", lambda e: _P())
    monkeypatch.setattr(
        loop, "get_model_entry",
        lambda n: ModelEntry(name="f", provider=ProviderType.OPENAI_COMPATIBLE,
                             model="f", base_url="http://x/v1"),
    )

    reg = ToolRegistry()
    reg.register(_InjectTool())
    ctx = AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes)
    session = Session(model_name="f")
    session.add_user("go")
    loop.run_agent_turn(session=session, ctx=ctx, registry=reg, model_name="f")

    injected = [m for m in session.messages if m.role == "user"
                and isinstance(m.content, list)
                and any(b.get("text") == "[injected]" for b in m.content)]
    assert injected, "extra_messages should have been appended to the session"
