"""`mbridge ask --image` —— vision 模型列举 + payload 组装 + 门禁。"""
from typer.testing import CliRunner

from modelbridge import cli
from modelbridge.cli import app
from modelbridge.models import Capabilities, ModelEntry
from modelbridge.schemas import ChatResponse

runner = CliRunner()


def _entry(name: str, vision: bool) -> ModelEntry:
    return ModelEntry(
        name=name,
        base_url="http://localhost:9/v1",
        model="m",
        capabilities=Capabilities(tools=False, vision=vision),
    )


class _CaptureProvider:
    last_request = None
    name = "fake"

    def chat(self, request, *, timeout, save_raw=False, verbose_label="chat"):  # noqa: ARG002
        _CaptureProvider.last_request = request
        return ChatResponse(content="看到一张图", elapsed_ms=1)


def test_vision_model_names(monkeypatch):
    monkeypatch.setattr(
        cli, "load_models_file",
        lambda: type("F", (), {"models": [_entry("glm-4v", True), _entry("ds", False)]})(),
    )
    assert cli._vision_model_names() == ["glm-4v"]


def test_ask_image_builds_multimodal_payload(monkeypatch, tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setattr(cli, "find_model", lambda name: _entry("glm-4v", True))
    monkeypatch.setattr(cli, "get_provider", lambda entry: _CaptureProvider())

    result = runner.invoke(
        app,
        ["ask", "这是什么", "--model", "glm-4v", "--image", str(png)],
        env={"MBRIDGE_HOME": str(tmp_path / "home")},
    )
    assert result.exit_code == 0, result.output
    req = _CaptureProvider.last_request
    assert req is not None
    content = req.messages[-1].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(b.get("type") == "image_url" for b in content)


def test_ask_image_rejected_for_non_vision_model(monkeypatch, tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setattr(cli, "find_model", lambda name: _entry("deepseek", False))
    monkeypatch.setattr(
        cli, "load_models_file",
        lambda: type("F", (), {"models": [_entry("glm-4v", True)]})(),
    )

    result = runner.invoke(
        app,
        ["ask", "这是什么", "--model", "deepseek", "--image", str(png)],
        env={"MBRIDGE_HOME": str(tmp_path / "home")},
    )
    assert result.exit_code == 2
    assert "glm-4v" in result.output
