from modelbridge.agent.session import Session


def test_add_user_with_images_builds_list_content():
    s = Session(model_name="m")
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    s.add_user("这是什么", images=[img])
    msg = s.messages[-1]
    assert msg.role == "user"
    assert msg.content == [{"type": "text", "text": "这是什么"}, img]


def test_add_user_without_images_stays_string():
    s = Session(model_name="m")
    s.add_user("hi")
    assert s.messages[-1].content == "hi"


def test_run_interactive_consumes_pending_images(monkeypatch):
    from pathlib import Path

    import modelbridge.agent.loop as loop
    from modelbridge.agent.context import AgentContext, auto_yes
    from modelbridge.agent.security import PathPolicy
    from modelbridge.agent.tools import ToolRegistry
    from modelbridge.models import ModelEntry, ProviderType
    from modelbridge.schemas import ChatResponse

    captured: dict = {}

    class _P:
        def chat(self, request, *, timeout, verbose_label=None):  # noqa: ARG002
            captured["messages"] = list(request.messages)
            return ChatResponse(content="ok")

    monkeypatch.setattr(loop, "get_provider", lambda e: _P())
    monkeypatch.setattr(
        loop, "get_model_entry",
        lambda n: ModelEntry(name="f", provider=ProviderType.OPENAI_COMPATIBLE,
                             model="f", base_url="http://x/v1"),
    )

    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    pending = {"images": [img]}
    inputs = iter(["看这张图"])

    def read_input():
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    session = Session(model_name="f")
    ctx = AgentContext(policy=PathPolicy([], []), cwd=Path.cwd(), approve=auto_yes)
    loop.run_interactive(
        session=session, ctx=ctx, registry=ToolRegistry(), model_name="f",
        read_input=read_input, pending_images=pending,
    )

    user_msgs = [m for m in captured["messages"] if m.role == "user"]
    assert user_msgs and isinstance(user_msgs[-1].content, list)
    assert user_msgs[-1].content[0]["type"] == "text"
    assert any(b.get("type") == "image_url" for b in user_msgs[-1].content)
    assert pending["images"] == []  # 消费后清空
