# 多模态图像识别 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ModelBridge 把图像喂给 vision 模型识别 —— 内联 `@图片`/`@paste`/图片 URL、单次 `ask --image`、AI 主动 `view_image`，全部一条消息内联送达。

**Architecture:** `ChatMessage.content` 由 `str` 扩成 `str | list[block]`（输入），`ChatResponse.content` 不变（输出仍 `str`）。新增 `text_of()` 在 4 个读消息内容处兜底。新增 `images.py` 统一编码为 OpenAI `image_url` 块。复用现有 `@` 提及管道（`mentions.py`），图像走图像分支。`view_image` 工具靠 `ToolResult.extra_messages` 把图作为 user 消息注入 agent 循环。provider 传输层零改动（已核查 9 个 adapter 透传数组）。

**Tech Stack:** Python 3.11（解释器 `py -3.11`）、pydantic v2、typer、rich、prompt_toolkit、Pillow（可选 extra，仅 `@paste`）。测试 `py -3.11 -m pytest`。

**Spec:** `docs/superpowers/specs/2026-06-19-multimodal-image-input-design.md`

**运行约定：** 所有测试/命令用 `py -3.11`（默认 `python` 是没 typer 的 venv）。提交粒度：每个 Task 末尾一次 commit。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `modelbridge/schemas.py` | 改 | `ChatMessage.content` 联合类型 + `text_of()` |
| `modelbridge/images.py` | 新增 | 图像源 → `image_url` 块；mime/体积守卫；剪贴板；`ensure_vision` |
| `modelbridge/context/windows.py` | 改 | `estimate_message_tokens` 走 `text_of` |
| `modelbridge/agent/commands.py` | 改 | 历史列表渲染走 `text_of` |
| `modelbridge/cli.py` | 改 | `_print_chat_dry_run`/`add_system` 走 `text_of`；`ask --image`；REPL 图像注入 + vision 门禁 |
| `modelbridge/prompt/builder.py` | 改 | `with_user_request(text, images=None)` |
| `modelbridge/agent/mentions.py` | 改 | `Attachment.kind/block`；图像扩展名分支；`@paste`；URL 识别；图像注入 |
| `modelbridge/agent/session.py` | 改 | `add_user(content, images=None)` |
| `modelbridge/agent/tools/base.py` | 改 | `ToolResult.extra_messages` |
| `modelbridge/agent/loop.py` | 改 | 追加 `extra_messages` 进 session |
| `modelbridge/agent/tools/image_tools.py` | 新增 | `ViewImageTool` |
| `modelbridge/agent/tools/registry.py` | 改 | vision 模型下注册 `view_image` |
| `pyproject.toml` | 改 | 可选 extra `vision = ["Pillow>=10"]` |
| `README.md` | 改 | 多模态小节 |

---

## Task 1: `text_of()` + `ChatMessage.content` 联合类型

**Files:**
- Modify: `modelbridge/schemas.py`
- Test: `tests/test_text_of.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_text_of.py
from modelbridge.schemas import ChatMessage, text_of


def test_text_of_str_passthrough():
    assert text_of("hello") == "hello"


def test_text_of_none_is_empty():
    assert text_of(None) == ""


def test_text_of_list_joins_text_blocks_ignores_images():
    content = [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "是什么"},
    ]
    assert text_of(content) == "看这张图 是什么"


def test_chatmessage_accepts_list_content_and_to_wire_passes_through():
    blocks = [
        {"type": "text", "text": "q"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    m = ChatMessage(role="user", content=blocks)
    wire = m.to_wire()
    assert wire["content"] == blocks  # 原样透传，未被字符串化
```

- [ ] **Step 2: 跑测试看它失败**

Run: `py -3.11 -m pytest tests/test_text_of.py -v`
Expected: FAIL（`text_of` 未定义 / pydantic 拒绝 list content）

- [ ] **Step 3: 实现**

`modelbridge/schemas.py`：把 `from typing import Any` 之后的 `ChatMessage.content` 改为联合类型，并在文件中加 `text_of`。

```python
# 顶部类型别名（紧接 imports 后）
ContentBlock = dict[str, Any]

# ChatMessage 内：
    content: str | list[ContentBlock] | None = None
```

`to_wire()` 不动（已用 `if self.content is not None` 守卫，list 原样进 dict）。在 `ChatMessage` 定义之后加：

```python
def text_of(content: "str | list[ContentBlock] | None") -> str:
    """把消息内容收敛成纯文本：str 原样；list 取 text 块拼接（空格分隔）、忽略图像块；None→''。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text")
            if isinstance(t, str) and t:
                parts.append(t)
    return " ".join(parts)
```

把 `text_of` 和 `ContentBlock` 加进 `__all__`。

- [ ] **Step 4: 跑测试看通过**

Run: `py -3.11 -m pytest tests/test_text_of.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归（确认联合类型没破坏现有）**

Run: `py -3.11 -m pytest -q`
Expected: 仍全绿（若有 estimate_tokens 报错说明 Task 2 的点已触发——记下，Task 2 修）

- [ ] **Step 6: Commit**

```bash
git add modelbridge/schemas.py tests/test_text_of.py
git commit -m "feat(schemas): widen ChatMessage.content to str|list[block] + text_of() helper"
```

---

## Task 2: 在 4 个消费点应用 `text_of`

**Files:**
- Modify: `modelbridge/context/windows.py:183`
- Modify: `modelbridge/agent/commands.py:161`
- Modify: `modelbridge/cli.py:512`, `modelbridge/cli.py:1101`
- Test: `tests/test_text_of_consumers.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_text_of_consumers.py
from modelbridge.schemas import ChatMessage
from modelbridge.context.windows import estimate_message_tokens


def test_estimate_message_tokens_handles_list_content():
    m = ChatMessage(role="user", content=[
        {"type": "text", "text": "描述这张图片里的内容"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ])
    # 不抛异常；按文本块估算 > 0
    assert estimate_message_tokens(m) > 0
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_text_of_consumers.py -v`
Expected: FAIL（`estimate_tokens` 收到 list 抛 TypeError）

- [ ] **Step 3: 改 4 处**

`modelbridge/context/windows.py` 顶部加 `from ..schemas import ChatMessage, text_of`（已 import ChatMessage 则只加 text_of），第 183 行附近：

```python
    if m.content:
        total += estimate_tokens(text_of(m.content))
```

`modelbridge/agent/commands.py:161`，加 import `from ..schemas import text_of`（与现有 schemas import 合并），改：

```python
            txt = text_of(m.content).replace("\n", " ").strip()
```

`modelbridge/cli.py` 顶部 schemas import 加入 `text_of`。第 512 行：

```python
        session.add_system(text_of(initial.messages[0].content) or sys_prompt_text)
```

第 1101 行（`_print_chat_dry_run`）：

```python
    text = "\n".join(text_of(m.content) for m in result.messages)
```

- [ ] **Step 4: 跑测试 + 回归**

Run: `py -3.11 -m pytest tests/test_text_of_consumers.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿

- [ ] **Step 5: Commit**

```bash
git add modelbridge/context/windows.py modelbridge/agent/commands.py modelbridge/cli.py tests/test_text_of_consumers.py
git commit -m "fix(content): route ChatMessage.content reads through text_of() (4 sites)"
```

---

## Task 3: `images.py` —— 图像源 → `image_url` 块

**Files:**
- Create: `modelbridge/images.py`
- Test: `tests/test_images.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_images.py
import base64
import pytest
from modelbridge import images


def test_text_block():
    assert images.text_block("hi") == {"type": "text", "text": "hi"}


def test_block_from_path_png(tmp_path):
    # 最小合法 PNG 头（魔数 \x89PNG）
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
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_images.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `modelbridge/images.py`**

```python
"""把图像来源（本地路径 / URL / 剪贴板）统一编码成 OpenAI ``image_url`` 内容块。

只有剪贴板路径用到 Pillow（懒导入，可选 extra ``modelbridge[vision]``）；
路径/URL 路径零额外依赖。
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

ContentBlock = dict[str, Any]

#: 编码后（base64 之前的原始字节）体积上限。base64 约膨胀 33%。
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
IMAGE_EXTS = frozenset(_EXT_MIME)

_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # 粗判，足够
    (b"BM", "image/bmp"),
]


class ImageError(Exception):
    """图像解析/编码失败，携带用户可读信息。"""


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
            f"图片过大 ({len(raw)//1024} KB > {MAX_IMAGE_BYTES//1024} KB)，请压缩后再发。"
        )
    mime = _mime_for(p, raw)
    return {"type": "image_url", "image_url": {"url": _data_url(raw, mime)}}


def block_from_url(url: str) -> ContentBlock:
    return {"type": "image_url", "image_url": {"url": url}}


def _import_imagegrab():
    try:
        from PIL import ImageGrab  # type: ignore
        return ImageGrab
    except Exception:  # noqa: BLE001 — 缺依赖即视为不可用
        return None


def block_from_clipboard() -> ContentBlock:
    grab = _import_imagegrab()
    if grab is None:
        raise ImageError('未安装 Pillow，无法读剪贴板图片。请运行: pip install "modelbridge[vision]"')
    img = grab.grabclipboard()
    # PIL 在剪贴板是文件列表时返回 list，是文本/空时返回 None。
    if img is None or isinstance(img, list):
        raise ImageError("剪贴板里没有图片。先截图（Win+Shift+S）再 @paste。")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError(f"剪贴板图片过大 ({len(raw)//1024} KB)，请压缩。")
    return {"type": "image_url", "image_url": {"url": _data_url(raw, "image/png")}}


def resolve_image_arg(arg: str) -> ContentBlock:
    """自动判别：http(s):// / data: → URL 块；否则按本地路径编码。"""
    low = arg.strip().lower()
    if low.startswith(("http://", "https://", "data:")):
        return block_from_url(arg.strip())
    return block_from_path(arg.strip())


def is_image_path(relpath: str) -> bool:
    return Path(relpath).suffix.lower() in IMAGE_EXTS


__all__ = [
    "ContentBlock", "ImageError", "MAX_IMAGE_BYTES", "IMAGE_EXTS",
    "text_block", "block_from_path", "block_from_url", "block_from_clipboard",
    "resolve_image_arg", "is_image_path",
]
```

- [ ] **Step 4: 跑测试看通过**

Run: `py -3.11 -m pytest tests/test_images.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add modelbridge/images.py tests/test_images.py
git commit -m "feat(images): image source -> OpenAI image_url block (path/url/clipboard, mime+size guard)"
```

---

## Task 4: Pillow 可选 extra

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 改 `[project.optional-dependencies]`**

在 `dev = [...]` 之后加：

```toml
vision = ["Pillow>=10.0"]
```

- [ ] **Step 2: 验证可解析**

Run: `py -3.11 -c "import tomllib,pathlib;tomllib.loads(pathlib.Path('pyproject.toml').read_text('utf-8'));print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add optional 'vision' extra (Pillow) for clipboard image paste"
```

---

## Task 5: vision 门禁 `ensure_vision`

**Files:**
- Modify: `modelbridge/images.py`
- Test: `tests/test_images.py` (extend)

设计：纯函数，调用方提供 vision 模型名列表，避免 images.py 耦合模型注册。

- [ ] **Step 1: 写失败测试（追加到 tests/test_images.py）**

```python
def test_ensure_vision_ok_when_capable():
    from modelbridge import images
    images.ensure_vision(has_images=True, model_is_vision=True, model_name="glm-4v",
                         available_vision=["glm-4v"])  # 不抛


def test_ensure_vision_rejects_non_vision_and_lists_models():
    from modelbridge import images
    with pytest.raises(images.ImageError) as e:
        images.ensure_vision(has_images=True, model_is_vision=False, model_name="deepseek",
                             available_vision=["glm-4v", "qwen-vl"])
    msg = str(e.value)
    assert "deepseek" in msg and "glm-4v" in msg and "qwen-vl" in msg


def test_ensure_vision_noop_without_images():
    from modelbridge import images
    images.ensure_vision(has_images=False, model_is_vision=False, model_name="x",
                         available_vision=[])  # 不抛
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_images.py -k ensure_vision -v`
Expected: FAIL

- [ ] **Step 3: 实现（追加到 images.py，并补进 __all__）**

```python
def ensure_vision(*, has_images: bool, model_is_vision: bool, model_name: str,
                  available_vision: list[str]) -> None:
    """带图但模型不支持 vision 时抛 ImageError，并提示可用 vision 模型。"""
    if not has_images or model_is_vision:
        return
    if available_vision:
        hint = "可用的 vision 模型: " + "、".join(available_vision) + "（用 --model / /model 切换）"
    else:
        hint = "models.yaml 里没有任何 capabilities.vision=true 的模型，请先添加一个。"
    raise ImageError(f"模型 '{model_name}' 未标记 vision，无法识别图片。{hint}")
```

- [ ] **Step 4: 跑测试看通过**

Run: `py -3.11 -m pytest tests/test_images.py -k ensure_vision -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add modelbridge/images.py tests/test_images.py
git commit -m "feat(images): ensure_vision() gate with available-model hint"
```

---

## Task 6: PromptBuilder 支持图像（`ask` 用）

**Files:**
- Modify: `modelbridge/prompt/builder.py`
- Test: `tests/test_builder_images.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_builder_images.py
from modelbridge.prompt.builder import PromptBuilder


def test_user_request_with_images_builds_list_content():
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    result = PromptBuilder().with_user_request("这是什么", images=[img]).build()
    user = result.messages[-1]
    assert user.role == "user"
    assert isinstance(user.content, list)
    assert user.content[0] == {"type": "text", "text": "这是什么"}
    assert user.content[1] == img


def test_user_request_without_images_stays_string():
    result = PromptBuilder().with_user_request("纯文本").build()
    assert result.messages[-1].content == "纯文本"


def test_images_do_not_change_stable_prefix_hash():
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    a = PromptBuilder().with_system_prompt("S").with_user_request("q").build()
    b = PromptBuilder().with_system_prompt("S").with_user_request("q", images=[img]).build()
    assert a.stable_prefix_hash == b.stable_prefix_hash
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_builder_images.py -v`
Expected: FAIL（`with_user_request` 不接受 images）

- [ ] **Step 3: 实现**

`builder.py`：给 `PromptBuilder` 加字段 `user_images: list[dict] = field(default_factory=list)`（放在 `user_request` 附近）。改 `with_user_request`：

```python
    def with_user_request(self, text: str | None, *, images: list[dict] | None = None) -> "PromptBuilder":
        self.user_request = text
        self.user_images = list(images or [])
        return self
```

在 `build()` 的「Final user message」段（约 316-322 行），把组装改为支持图像：

```python
        if self.user_request is not None and self.user_request.strip():
            files_block = sections.get("project_files", "").strip()
            if files_block:
                user_body = f"{files_block}\n\n{self.user_request}"
            else:
                user_body = self.user_request
            if self.user_images:
                from ..schemas import text_block as _tb  # 见下注
                content: list[dict] = [{"type": "text", "text": user_body}, *self.user_images]
                messages.append(ChatMessage(role="user", content=content))
            else:
                messages.append(ChatMessage(role="user", content=user_body))
```

> 注：`text_block` 已在 `images.py`；为避免 builder→images 依赖，这里直接内联 `{"type":"text","text":...}`（与 `images.text_block` 同形）。无需 import。删除上面那行 `from ..schemas import text_block`——它只是占位说明，最终代码用内联字面量。

`hashes` 段用的 `sections["user_request"]`（字符串）不变，所以 `dynamic_suffix_hash` 仍基于文本，stable prefix 不受影响。✓

- [ ] **Step 4: 跑测试看通过 + 回归**

Run: `py -3.11 -m pytest tests/test_builder_images.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿

- [ ] **Step 5: Commit**

```bash
git add modelbridge/prompt/builder.py tests/test_builder_images.py
git commit -m "feat(builder): with_user_request(images=...) builds multimodal user message"
```

---

## Task 7: `mbridge ask --image`

**Files:**
- Modify: `modelbridge/cli.py`（`cmd_ask`）
- Test: `tests/test_ask_image.py` (new)

需要一个「列出 vision 模型名」的小助手。先加它，再接线。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ask_image.py
from modelbridge import images


def test_collect_vision_model_names(monkeypatch):
    from modelbridge import cli
    class _Cap: 
        def __init__(self, v): self.vision = v
    class _M:
        def __init__(self, name, v): self.name = name; self.capabilities = _Cap(v)
    monkeypatch.setattr(cli, "load_models", lambda: type("F", (), {"models": [_M("glm-4v", True), _M("ds", False)]})())
    assert cli._vision_model_names() == ["glm-4v"]
```

> 若 `cli.load_models` 名称不同（用 `list_models` / `load_models_file`），按实际 import 调整这个测试和实现。

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_ask_image.py -v`
Expected: FAIL（`_vision_model_names` 不存在）

- [ ] **Step 3: 实现 helper + `--image` 选项**

先在 cli.py 顶部确保 `from . import images` 可用，并加 helper（放在 `cmd_ask` 之前）：

```python
def _vision_model_names() -> list[str]:
    try:
        mf = load_models()  # 复用现有模型注册加载（按实际函数名）
        return [m.name for m in mf.models if getattr(m.capabilities, "vision", False)]
    except Exception:  # noqa: BLE001
        return []
```

给 `cmd_ask` 签名加（放在 `max_context` 之前）：

```python
    image: Optional[List[str]] = typer.Option(
        None, "--image",
        help="附加图片（本地路径或 http(s):// / data: URL，可重复）。需模型 capabilities.vision=true。",
    ),
```

在 `cmd_ask` 体内，`builder = PromptBuilder().with_user_request(prompt)`（约 1208 行）**之前**解析图片块：

```python
    image_blocks: list[dict] = []
    if image:
        for arg in image:
            try:
                image_blocks.append(images.resolve_image_arg(arg))
            except images.ImageError as e:
                err_console.print(f"[red]{e}[/red]")
                raise typer.Exit(code=2)
```

把 `with_user_request(prompt)` 改为 `with_user_request(prompt, images=image_blocks)`（仅非路由分支；路由分支本期不支持 `--image`，若同时传则报错）。在函数早段的互斥检查处补：

```python
    if image and (use_route or auto):
        err_console.print("[red]--image 暂不支持与 --route/--auto 同用；请显式 --model 指定 vision 模型。[/red]")
        raise typer.Exit(code=2)
```

在真正发请求前（模型 `entry` 已解析处）加 vision 门禁：

```python
        if image_blocks:
            try:
                images.ensure_vision(
                    has_images=True,
                    model_is_vision=bool(getattr(entry.capabilities, "vision", False)),
                    model_name=entry.name,
                    available_vision=_vision_model_names(),
                )
            except images.ImageError as e:
                err_console.print(f"[red]{e}[/red]")
                raise typer.Exit(code=2)
```

> 执行者：用 Read 看 `cmd_ask` 里 `entry = ...` 解析的确切位置，把门禁插在「entry 已知」且「发请求之前」。

- [ ] **Step 4: 集成测试：payload 捕获 + 门禁**

```python
# 追加到 tests/test_ask_image.py —— 用 typer.testing.CliRunner 跑 ask，
# monkeypatch provider.chat 捕获 request.messages，断言末条 user content 含 image_url 块。
# 若现有测试已有 ask 的 runner fixture，复用其 fake provider 模式。
```

> 执行者：参照 `tests/` 里已有的 ask/provider 假桩测试写法（grep `CliRunner` 或 `monkeypatch.*chat`），断言：(a) 带 `--image <tmp.png>` + vision 模型 → request 末条 user.content 是 `[text, image_url]`；(b) 非 vision 模型 + `--image` → 退出码 2 且 stderr 提示可用模型。

- [ ] **Step 5: 跑测试 + 回归**

Run: `py -3.11 -m pytest tests/test_ask_image.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿

- [ ] **Step 6: Commit**

```bash
git add modelbridge/cli.py tests/test_ask_image.py
git commit -m "feat(ask): --image flag (path/url) with vision gate"
```

---

## Task 8: `@mention` 图像分支 + `@paste` + URL 识别

**Files:**
- Modify: `modelbridge/agent/mentions.py`
- Test: `tests/test_mentions_images.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mentions_images.py
from modelbridge.agent.mentions import resolve_mentions, collect_image_blocks
from modelbridge.project.file_index import FileIndex, FileEntry


def _index(*relpaths):
    idx = FileIndex.__new__(FileIndex)
    idx.entries = [FileEntry(relpath=r, is_dir=False) for r in relpaths]
    idx.truncated = False
    return idx


def test_image_mention_yields_image_attachment(tmp_path):
    img = tmp_path / "diagram.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    idx = _index("diagram.png")
    res = resolve_mentions("@diagram.png 这是什么", idx, project_root=tmp_path)
    atts = [a for a in res.attachments if a.kind == "image"]
    assert len(atts) == 1
    assert atts[0].block["type"] == "image_url"


def test_text_mention_still_text(tmp_path):
    code = tmp_path / "a.py"
    code.write_text("print(1)", encoding="utf-8")
    idx = _index("a.py")
    res = resolve_mentions("@a.py", idx, project_root=tmp_path)
    assert res.attachments and res.attachments[0].kind == "file"


def test_paste_pseudo_mention(monkeypatch, tmp_path):
    import modelbridge.agent.mentions as M
    fake = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    monkeypatch.setattr(M.images, "block_from_clipboard", lambda: fake)
    res = resolve_mentions("@paste 看这个", _index(), project_root=tmp_path)
    assert any(a.kind == "image" and a.block == fake for a in res.attachments)


def test_collect_image_blocks_helper(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = resolve_mentions("@x.png hi", _index("x.png"), project_root=tmp_path)
    blocks = collect_image_blocks(res)
    assert blocks and blocks[0]["type"] == "image_url"
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_mentions_images.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 mentions.py 改动**

顶部加 `from .. import images`。给 `Attachment` 加字段：

```python
@dataclass
class Attachment:
    relpath: str
    kind: str  # "file" | "dir" | "image"
    content: str = ""
    truncated: bool = False
    skipped_reason: str | None = None
    block: dict | None = None   # kind=="image" 时携带 image_url 块
```

在 `resolve_mentions` 循环里，先特判 `@paste`/`@clipboard`，再对图像扩展名走图像分支：

```python
    for mention in find_mentions(text):
        norm = _normalize_token(mention.token)
        if not norm:
            continue
        if norm.lower() in ("paste", "clipboard"):
            try:
                blk = images.block_from_clipboard()
                result.attachments.append(Attachment(relpath="@paste", kind="image", block=blk))
            except images.ImageError as e:
                result.unresolved.append(f"paste: {e}")
            continue
        entry = _resolve_token(norm, index)
        if entry is None:
            result.unresolved.append(mention.token)
            continue
        if entry.relpath in seen:
            continue
        seen.add(entry.relpath)
        if entry.is_dir:
            result.attachments.append(_dir_attachment(entry, index, max_dir_entries))
        elif images.is_image_path(entry.relpath):
            try:
                blk = images.block_from_path(str((project_root / entry.relpath)))
                result.attachments.append(Attachment(relpath=entry.relpath, kind="image", block=blk))
            except images.ImageError as e:
                result.attachments.append(Attachment(relpath=entry.relpath, kind="file",
                                                     content="", skipped_reason=str(e)))
        else:
            result.attachments.append(_file_attachment(entry, project_root))
```

URL 识别 + helper（文件末尾）：

```python
import re as _re
_IMG_URL_RE = _re.compile(r"https?://\S+\.(?:png|jpe?g|gif|webp)(?:\?\S*)?", _re.IGNORECASE)

def collect_image_blocks(resolved: "ResolvedMentions") -> list[dict]:
    """从已解析结果 + 原文 URL 里收集所有图像块（供 REPL 并入用户消息）。"""
    blocks = [a.block for a in resolved.attachments if a.kind == "image" and a.block]
    for m in _IMG_URL_RE.finditer(resolved.text or ""):
        blocks.append(images.block_from_url(m.group(0)))
    return blocks
```

`build_injection_messages`：在循环开头跳过 image 附件（它们不走文本注入）：

```python
    for att in resolved.attachments:
        if att.kind == "image":
            continue
        ...
```

把 `collect_image_blocks` 加进 `__all__`。

- [ ] **Step 4: 跑测试 + 回归**

Run: `py -3.11 -m pytest tests/test_mentions_images.py tests/test_mentions.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿（原 `test_mentions.py` 不回归）

- [ ] **Step 5: Commit**

```bash
git add modelbridge/agent/mentions.py tests/test_mentions_images.py
git commit -m "feat(mentions): @image / @paste / image-URL -> inline image blocks"
```

---

## Task 9: Session.add_user 图像 + REPL 内联注入 + 门禁

**Files:**
- Modify: `modelbridge/agent/session.py`（`add_user`）
- Modify: `modelbridge/agent/loop.py`（`run_interactive` 的 `add_user` 调用）
- Modify: `modelbridge/cli.py`（`_apply_mentions` / `read_input`）
- Test: `tests/test_session_images.py` (new)

- [ ] **Step 1: 写失败测试（Session）**

```python
# tests/test_session_images.py
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
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_session_images.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 Session.add_user**

`session.py`：

```python
    def add_user(self, content: str, images: list[dict] | None = None) -> None:
        if images:
            blocks = [{"type": "text", "text": content}, *images]
            self.messages.append(ChatMessage(role="user", content=blocks))
        else:
            self.messages.append(ChatMessage(role="user", content=content))
```

- [ ] **Step 4: REPL 接线（cli.py + loop.py）**

`loop.py` `run_interactive`：`read_input` 返回纯文本，图像块需另行传递。最小改动方案——在闭包间用一个可变 dict 传递本轮图像块：

在 cli.py `_run_repl` 里加 `pending_turn: dict[str, Any] = {"images": []}`，改 `_apply_mentions` 收集图像块并存入，同时做门禁：

```python
    def _apply_mentions(text: str) -> bool:
        """注入文本附件；收集图像块到 pending_turn['images']；返回 True=可继续，False=本轮被门禁拦下。"""
        from .agent.mentions import inject_file_mentions, resolve_mentions, collect_image_blocks
        from . import images as _img
        index = _get_file_index()
        # 解析（即便无 index，@paste / URL 仍可用）
        resolved = resolve_mentions(text, index, project_root=cwd_resolved) if index else None
        # 文本附件注入（沿用现有函数；它内部会再 resolve 一次——可接受，或改为复用 resolved）
        if index is not None:
            inj = inject_file_mentions(text, index, session, project_root=cwd_resolved)
        img_blocks = collect_image_blocks(resolved) if resolved else _url_only_blocks(text)
        if img_blocks:
            entry = find_model(model_name)
            try:
                _img.ensure_vision(
                    has_images=True,
                    model_is_vision=bool(getattr(entry.capabilities, "vision", False)) if entry else False,
                    model_name=model_name,
                    available_vision=_vision_model_names(),
                )
            except _img.ImageError as e:
                console.print(f"[red]{e}[/red]")
                pending_turn["images"] = []
                return False
            pending_turn["images"] = img_blocks
            console.print(f"[dim]🖼 已内联 {len(img_blocks)} 张图片到本轮消息[/dim]")
        return True
```

其中 `_url_only_blocks(text)` 是无 index 时仅扫 URL 的兜底（可直接调 `collect_image_blocks` 配一个空 ResolvedMentions；执行者择简实现）。

`read_input`：把 `_apply_mentions(text)` 的返回值用于门禁——被拦下则返回 `""`（loop 跳过空输入）：

```python
        if stripped and not stripped.startswith("/"):
            try:
                if not _apply_mentions(text):
                    return ""   # vision 门禁拦下本轮
            except Exception:
                pass
        return text
```

`loop.py` `run_interactive` 里 `session.add_user(text)` 改为带图像（图像块由 cli 经一个回调/共享态提供）。最简：给 `run_interactive` 加可选参 `pending_images: dict | None = None`，调用处传 `pending_turn`，并：

```python
        if on_user_echo is not None:
            on_user_echo(text)
        imgs = (pending_images or {}).get("images") if pending_images else None
        session.add_user(text, images=imgs or None)
        if pending_images is not None:
            pending_images["images"] = []   # 消费后清空
```

cli.py 调 `run_interactive(...)` 处传入 `pending_images=pending_turn`。

> 执行者：用 Read 打开 `loop.py:163-305` 与 cli.py 调 `run_interactive` 的实参列表，按现有 kwargs 风格插入 `pending_images`。保持纯文本路径行为不变（imgs 为空时 add_user 老行为）。

- [ ] **Step 5: 测试 loop 注入（轻量单测）**

```python
# 追加到 tests/test_session_images.py：构造一个最小 run_interactive 调用，
# read_input 依次返回 "@x.png hi" 再 EOF；用假 provider；断言发出的 user 消息 content 是 list。
# 若 run_interactive 测试成本高，至少断言 add_user(images=...) 经 pending dict 流转的纯逻辑。
```

> 执行者：参照 `tests/` 现有 run_interactive/REPL 测试（grep `run_interactive`）。若无现成夹具，退而测 `add_user` + `collect_image_blocks` 的组合逻辑即可，REPL 端到端留给 Task 13 手动冒烟。

- [ ] **Step 6: 跑测试 + 回归**

Run: `py -3.11 -m pytest tests/test_session_images.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿

- [ ] **Step 7: Commit**

```bash
git add modelbridge/agent/session.py modelbridge/agent/loop.py modelbridge/cli.py tests/test_session_images.py
git commit -m "feat(repl): inline @image/@paste/url into the user turn + vision gate"
```

---

## Task 10: `ToolResult.extra_messages` + loop 追加

**Files:**
- Modify: `modelbridge/agent/tools/base.py`
- Modify: `modelbridge/agent/loop.py`（`run_agent_turn` 工具分发段）
- Test: `tests/test_extra_messages.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_extra_messages.py
from modelbridge.agent.tools.base import Tool, ToolResult
from modelbridge.schemas import ChatMessage


def test_toolresult_has_extra_messages_default_none():
    r = ToolResult(content="ok")
    assert r.extra_messages is None


def test_toolresult_carries_extra_messages():
    m = ChatMessage(role="user", content=[{"type": "text", "text": "img"}])
    r = ToolResult(content="loaded", extra_messages=[m])
    assert r.extra_messages == [m]
```

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_extra_messages.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`base.py` `ToolResult` dataclass 加字段：

```python
    extra_messages: "list | None" = None  # loop 在 tool_result 之后追加的额外消息（如图像 user 消息）
```

`loop.py` `run_agent_turn` 工具分发段（约 131-137 行），在 `session.add_tool_result(...)` 之后：

```python
                result = registry.dispatch(call, ctx)
                session.add_tool_result(tool_call_id=call.id, content=result.content)
                for extra in (getattr(result, "extra_messages", None) or []):
                    session.messages.append(extra)
                answered.add(call.id)
```

- [ ] **Step 4: 跑测试 + 回归**

Run: `py -3.11 -m pytest tests/test_extra_messages.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿

- [ ] **Step 5: Commit**

```bash
git add modelbridge/agent/tools/base.py modelbridge/agent/loop.py tests/test_extra_messages.py
git commit -m "feat(tools): ToolResult.extra_messages + loop appends them after tool result"
```

---

## Task 11: `view_image` 工具 + 条件注册

**Files:**
- Create: `modelbridge/agent/tools/image_tools.py`
- Modify: `modelbridge/agent/tools/registry.py`
- Test: `tests/test_view_image_tool.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_view_image_tool.py
from pathlib import Path
from modelbridge.agent.tools.image_tools import ViewImageTool
from modelbridge.agent.context import AgentContext
from modelbridge.agent.security import PathPolicy


def _ctx(tmp_path):
    return AgentContext(policy=PathPolicy(allowed_dirs=[tmp_path]), cwd=tmp_path)


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
```

> 执行者：用 Read 确认 `PathPolicy` 构造签名（`allowed_dirs=` 是否正确）与 `AgentContext` 必填字段；按实际调整 `_ctx`。

- [ ] **Step 2: 跑测试看失败**

Run: `py -3.11 -m pytest tests/test_view_image_tool.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 image_tools.py**

```python
"""``view_image`` —— 让 AI 主动加载一张本地图片来"看"。

OpenAI 兼容的 role=tool 消息只能纯文本，所以工具返回一句文本确认 +
通过 ToolResult.extra_messages 追加一条携带 image_url 块的 user 消息，
下一轮模型即可看到图。
"""
from __future__ import annotations

from typing import Any

from ...schemas import ChatMessage
from ... import images
from .base import Tool, ToolResult
from ..context import AgentContext


class ViewImageTool(Tool):
    name = "view_image"
    description = (
        "加载一张本地图片让你能看到它的内容（仅 vision 模型可用）。"
        "参数 path 为相对/绝对图片路径。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "图片文件路径（png/jpg/gif/webp/bmp）"}
            },
            "required": ["path"],
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        path = str(args.get("path") or "").strip()
        if not path:
            return self.err("view_image 需要 path 参数。")
        try:
            resolved = ctx.resolve(path)  # 经 PathPolicy：越界 / 敏感文件会抛
        except Exception as e:  # noqa: BLE001 — 安全策略异常
            return self.err(f"路径不被允许: {e}")
        try:
            block = images.block_from_path(str(resolved))
        except images.ImageError as e:
            return self.err(str(e))
        name = resolved.name
        msg = ChatMessage(role="user", content=[
            {"type": "text", "text": f"[view_image 加载的图片: {name}]"},
            block,
        ])
        return ToolResult(content=f"已加载图片 {name}，见下一条消息。", extra_messages=[msg])
```

- [ ] **Step 4: 条件注册（registry.py）**

`build_default_registry` 加参 `include_view_image: bool = False`：

```python
def build_default_registry(*, include_bash: bool = False, include_view_image: bool = False) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(ListDirTool())
    reg.register(WriteFileTool())
    reg.register(StrReplaceTool())
    if include_bash:
        reg.register(RunBashTool())
    if include_view_image:
        from .image_tools import ViewImageTool
        reg.register(ViewImageTool())
    return reg
```

cli.py 构建 registry 处：当当前模型 `capabilities.vision` 为真时传 `include_view_image=True`。

> 执行者：grep `build_default_registry(` 在 cli.py 的调用点，按 `entry.capabilities.vision` 设该参。

测试（追加 tests/test_view_image_tool.py）：

```python
def test_registry_includes_view_image_only_when_requested():
    from modelbridge.agent.tools.registry import build_default_registry
    assert "view_image" not in build_default_registry().names()
    assert "view_image" in build_default_registry(include_view_image=True).names()
```

- [ ] **Step 5: 跑测试 + 回归**

Run: `py -3.11 -m pytest tests/test_view_image_tool.py -q && py -3.11 -m pytest -q`
Expected: PASS，全绿

- [ ] **Step 6: Commit**

```bash
git add modelbridge/agent/tools/image_tools.py modelbridge/agent/tools/registry.py modelbridge/cli.py tests/test_view_image_tool.py
git commit -m "feat(tools): view_image tool (vision-gated registration) via extra_messages"
```

---

## Task 12: 文档

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 加多模态小节**

在 README 命令/功能区加：
- `mbridge ask --image <path|url> "问题"`（可重复）。
- REPL 内联：`@图片文件`、`@paste`（剪贴板，需 `pip install "modelbridge[vision]"`）、消息里的图片 URL 自动识别。
- AI 主动读图：vision 模型下自动启用 `view_image` 工具。
- 如何让模型支持：`models.yaml` 里该模型 `capabilities.vision: true`；不支持时带图会被拒绝并列出可用 vision 模型。
- 备注：图像 token 未计入本地估算，实际以 provider 账单为准。

在现有 `@` 提及说明处补一句"图片文件会作为图像内联识别"。

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): document multimodal image input (@image/@paste/url, ask --image, view_image)"
```

---

## Task 13: 全量验证

- [ ] **Step 1: 全量测试**

Run: `py -3.11 -m pytest -q`
Expected: 全绿

- [ ] **Step 2: 类型 + lint**

Run: `py -3.11 -m mypy modelbridge/images.py modelbridge/schemas.py modelbridge/agent/tools/image_tools.py ; py -3.11 -m ruff check modelbridge/`
Expected: 无新增错误（mypy 项目级 `check_untyped_defs=false`，关注 images/schemas/image_tools 无硬错）

- [ ] **Step 3: 手动冒烟（编码路径，无需真模型）**

Run:
```bash
py -3.11 -c "from modelbridge import images; import pathlib; print(images.resolve_image_arg('https://x/a.png'))"
```
Expected: 打印 image_url 块。

> 若手头有真 vision 模型（GLM-4V / Qwen-VL），用 `mbridge ask --model <vision> --image <真实截图.png> '这张图里有什么'` 验证端到端，并把验证过的型号记进 spec §9 风险点 1。

- [ ] **Step 4: 无新增改动则结束（提交已在各 Task 完成）**

---

## Self-Review 已执行

- **Spec 覆盖**：§4.1→T1/T2；§4.2→T3；§4.3→T5；§4.4→T6/T7；§4.5→T8/T9；§4.6→T10/T11；§4.7→T2；§5→T4；§6→T2；§7→各 Task 测试；§8→T12。无遗漏。
- **类型一致**：`text_of`、`ImageError`、`resolve_image_arg`、`is_image_path`、`collect_image_blocks`、`ensure_vision`、`Attachment.block`、`ToolResult.extra_messages`、`add_user(images=)`、`with_user_request(images=)`、`build_default_registry(include_view_image=)` 跨任务签名一致。
- **占位扫描**：Task 7/9/11 有「执行者用 Read 确认实际签名」标注——这是因 cli.py/loop.py 的精确插入点需对照现网代码，非占位逃避；每处都给了完整目标代码，仅插入位置需现场核对。
