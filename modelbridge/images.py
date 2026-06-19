"""把图像来源（本地路径 / URL / 剪贴板）统一编码成 OpenAI ``image_url`` 内容块。

只有剪贴板路径用到 Pillow（懒导入，可选 extra ``modelbridge[vision]``）；
路径 / URL 路径零额外依赖。

返回的块形如::

    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

可直接放进 :class:`modelbridge.schemas.ChatMessage` 的 list content。
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

ContentBlock = dict[str, Any]

#: 原始字节体积上限（base64 之前）。base64 约膨胀 33%。
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
IMAGE_EXTS = frozenset(_EXT_MIME)

# (magic prefix, mime) — 用于扩展名缺失/错误时兜底探测。
_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # 粗判 RIFF 容器，足够日常用
    (b"BM", "image/bmp"),
]


class ImageError(Exception):
    """图像解析 / 编码失败，携带用户可读信息。"""


def text_block(text: str) -> ContentBlock:
    return {"type": "text", "text": text}


def _mime_for(path: Path, raw: bytes) -> str:
    ext = path.suffix.lower()
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    for sig, mime in _MAGIC:
        if raw.startswith(sig):
            return mime
    raise ImageError(f"无法识别图片格式: {path.name}（支持 png/jpg/gif/webp/bmp）")


def _data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def block_from_path(path: str) -> ContentBlock:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ImageError(f"图片文件不存在: {path}")
    raw = p.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError(
            f"图片过大 ({len(raw) // 1024} KB > {MAX_IMAGE_BYTES // 1024} KB)，请压缩后再发。"
        )
    mime = _mime_for(p, raw)
    return {"type": "image_url", "image_url": {"url": _data_url(raw, mime)}}


def block_from_url(url: str) -> ContentBlock:
    return {"type": "image_url", "image_url": {"url": url}}


def _import_imagegrab():  # pragma: no cover - thin import shim, patched in tests
    try:
        from PIL import ImageGrab  # type: ignore

        return ImageGrab
    except Exception:  # noqa: BLE001 — 缺依赖即视为不可用
        return None


def block_from_clipboard() -> ContentBlock:
    grab = _import_imagegrab()
    if grab is None:
        raise ImageError(
            '未安装 Pillow，无法读剪贴板图片。请运行: pip install "modelbridge[vision]"'
        )
    img = grab.grabclipboard()
    # PIL 在剪贴板是文件列表时返回 list，是文本/空时返回 None。
    if img is None or isinstance(img, list):
        raise ImageError("剪贴板里没有图片。先截图（Win+Shift+S）再 @paste。")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError(f"剪贴板图片过大 ({len(raw) // 1024} KB)，请压缩。")
    return {"type": "image_url", "image_url": {"url": _data_url(raw, "image/png")}}


def resolve_image_arg(arg: str) -> ContentBlock:
    """自动判别：http(s):// / data: → URL 块；否则按本地路径编码。"""
    s = arg.strip()
    low = s.lower()
    if low.startswith(("http://", "https://", "data:")):
        return block_from_url(s)
    return block_from_path(s)


def is_image_path(relpath: str) -> bool:
    return Path(relpath).suffix.lower() in IMAGE_EXTS


def ensure_vision(
    *,
    has_images: bool,
    model_is_vision: bool,
    model_name: str,
    available_vision: list[str],
) -> None:
    """带图但模型不支持 vision 时抛 :class:`ImageError`，并提示可用 vision 模型。"""
    if not has_images or model_is_vision:
        return
    if available_vision:
        hint = "可用的 vision 模型: " + "、".join(available_vision) + "（用 --model / /model 切换）"
    else:
        hint = "models.yaml 里没有任何 capabilities.vision=true 的模型，请先添加一个。"
    raise ImageError(f"模型 '{model_name}' 未标记 vision，无法识别图片。{hint}")


__all__ = [
    "ContentBlock",
    "ImageError",
    "MAX_IMAGE_BYTES",
    "IMAGE_EXTS",
    "text_block",
    "block_from_path",
    "block_from_url",
    "block_from_clipboard",
    "resolve_image_arg",
    "is_image_path",
    "ensure_vision",
]
