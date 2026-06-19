"""read_file 直接读图：vision 模型给图像块，非 vision 给文字说明；文本文件不受影响。"""
from pathlib import Path

from modelbridge.agent.context import AgentContext
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.file_tools import ReadFileTool
from modelbridge.agent.tools.registry import build_default_registry


def _ctx(tmp_path: Path, *, vision: bool) -> AgentContext:
    policy = PathPolicy(allowed_dirs=[tmp_path], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path, model_is_vision=vision)


def test_read_image_with_vision_returns_image_block(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    res = ReadFileTool().execute({"path": "a.png"}, _ctx(tmp_path, vision=True))
    assert not res.is_error
    assert res.extra_messages and res.extra_messages[0].role == "user"
    blocks = res.extra_messages[0].content
    assert any(b.get("type") == "image_url" for b in blocks)


def test_read_image_without_vision_returns_note(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    res = ReadFileTool().execute({"path": "a.png"}, _ctx(tmp_path, vision=False))
    assert not res.is_error
    assert res.extra_messages is None
    assert "图片" in res.content and "vision" in res.content


def test_read_text_file_unaffected(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("print('hi')", encoding="utf-8")
    res = ReadFileTool().execute({"path": "code.py"}, _ctx(tmp_path, vision=True))
    assert not res.is_error
    assert res.content == "print('hi')"
    assert res.extra_messages is None


def test_registry_has_no_view_image():
    assert "view_image" not in build_default_registry().names()
    assert "read_file" in build_default_registry().names()
