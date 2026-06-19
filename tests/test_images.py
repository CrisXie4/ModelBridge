import base64
import pytest
from modelbridge import images


def test_text_block():
    assert images.text_block("hi") == {"type": "text", "text": "hi"}


def test_block_from_path_png(tmp_path):
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    f = tmp_path / "a.png"
    f.write_bytes(raw)
    block = images.block_from_path(str(f))
    assert block["type"] == "image_url"
    url = block["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == raw


def test_block_from_path_missing(tmp_path):
    with pytest.raises(images.ImageError):
        images.block_from_path(str(tmp_path / "nope.png"))


def test_block_from_path_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 10)
    f = tmp_path / "big.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    with pytest.raises(images.ImageError):
        images.block_from_path(str(f))


def test_block_from_url_passthrough():
    b = images.block_from_url("https://x.com/a.jpg")
    assert b == {"type": "image_url", "image_url": {"url": "https://x.com/a.jpg"}}


def test_resolve_image_arg_dispatches():
    assert images.resolve_image_arg("https://x/a.png")["image_url"]["url"] == "https://x/a.png"
    assert images.resolve_image_arg("data:image/png;base64,AAAA")["image_url"]["url"].startswith("data:")


def test_block_from_clipboard_missing_pillow(monkeypatch):
    monkeypatch.setattr(images, "_import_imagegrab", lambda: None)
    with pytest.raises(images.ImageError) as e:
        images.block_from_clipboard()
    assert "Pillow" in str(e.value)


def test_is_image_path():
    assert images.is_image_path("a/b/diagram.PNG")
    assert not images.is_image_path("a/b/code.py")


def test_ensure_vision_ok_when_capable():
    images.ensure_vision(has_images=True, model_is_vision=True, model_name="glm-4v",
                         available_vision=["glm-4v"])


def test_ensure_vision_rejects_non_vision_and_lists_models():
    with pytest.raises(images.ImageError) as e:
        images.ensure_vision(has_images=True, model_is_vision=False, model_name="deepseek",
                             available_vision=["glm-4v", "qwen-vl"])
    msg = str(e.value)
    assert "deepseek" in msg and "glm-4v" in msg and "qwen-vl" in msg


def test_ensure_vision_noop_without_images():
    images.ensure_vision(has_images=False, model_is_vision=False, model_name="x",
                         available_vision=[])
