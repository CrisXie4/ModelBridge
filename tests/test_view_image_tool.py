from pathlib import Path

from modelbridge.agent.context import AgentContext
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.image_tools import ViewImageTool
from modelbridge.agent.tools.registry import build_default_registry


def _ctx(tmp_path: Path) -> AgentContext:
    policy = PathPolicy(allowed_dirs=[tmp_path], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path)


def test_view_image_returns_extra_image_message(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    res = ViewImageTool().execute({"path": "a.png"}, _ctx(tmp_path))
    assert not res.is_error
    assert res.extra_messages and res.extra_messages[0].role == "user"
    blocks = res.extra_messages[0].content
    assert any(b.get("type") == "image_url" for b in blocks)


def test_view_image_missing_file(tmp_path):
    res = ViewImageTool().execute({"path": "nope.png"}, _ctx(tmp_path))
    assert res.is_error


def test_view_image_path_denied(tmp_path):
    # 越界路径（allowed_dirs 只含 tmp_path）→ 工具报错，不读盘
    res = ViewImageTool().execute({"path": str(tmp_path.parent / "outside.png")}, _ctx(tmp_path))
    assert res.is_error


def test_registry_includes_view_image_only_when_requested():
    assert "view_image" not in build_default_registry().names()
    assert "view_image" in build_default_registry(include_view_image=True).names()
