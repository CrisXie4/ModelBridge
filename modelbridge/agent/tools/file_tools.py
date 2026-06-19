"""File-system tools.

Read-only tools (``read_file``, ``list_dir``) just enforce :class:`PathPolicy`.
Mutating tools (``write_file``, ``str_replace``) additionally require
``ctx.confirm(...)`` before touching anything.

All tools cap output size so a 10 MB log file can't blow up the context
window — they return a clear "truncated" notice when that happens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import images
from ...schemas import ChatMessage
from ..context import AgentContext
from ..security import PathDenied
from .base import Tool, ToolResult


# Size caps removed by user request: AI may read / write any-size file
# inside ``allowed_dirs``. Path-policy (allowed_dirs + blocked patterns
# like .env / id_rsa / .ssh) still applies and is enforced *before* any
# disk I/O happens. Write & str_replace still require ``ctx.confirm``.
_MAX_LIST_ENTRIES = 500   # only kept for list_dir — a 50K-entry dump would
                          # blow up the LLM context window with no benefit.


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读取项目中的文件内容，返回完整 UTF-8 文本（不做大小截断）。"
        "仅在 allowed_project_dirs 之内且未命中 block_sensitive_files 时允许。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径，可以是绝对路径或相对于 cwd 的路径。",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        path_arg = args.get("path")
        if not isinstance(path_arg, str) or not path_arg:
            return self.err("缺少必填参数 path")
        try:
            resolved = ctx.resolve(path_arg)
        except PathDenied as e:
            return self.err(str(e))

        if not resolved.exists():
            return self.err(f"文件不存在: {resolved}")
        if resolved.is_dir():
            return self.err(f"{resolved} 是目录，请使用 list_dir。")

        # Images: read them as *pictures*, not garbled text. A vision model
        # gets the image as an image block (injected as a follow-up user
        # message, since role=tool can't carry images); a non-vision model
        # gets a clear note instead of decoded bytes.
        if images.is_image_path(resolved.name):
            return self._read_image(resolved, ctx)

        try:
            data = resolved.read_bytes()
        except OSError as e:
            return self.err(f"读取失败: {e}")

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            return self.ok(
                f"[警告: 文件不是有效 UTF-8，已用 replace 解码]\n\n{text}",
                structured={"path": str(resolved), "bytes": len(data)},
            )

        return self.ok(text, structured={"path": str(resolved), "bytes": len(data)})

    def _read_image(self, resolved: Path, ctx: AgentContext) -> ToolResult:
        if not getattr(ctx, "model_is_vision", False):
            return self.ok(
                f"[{resolved.name} 是图片文件，当前模型不支持 vision，无法识别图像内容。"
                f"换一个 capabilities.vision=true 的模型再读。]",
                structured={"path": str(resolved), "image": True, "vision": False},
            )
        try:
            block = images.block_from_path(str(resolved))
        except images.ImageError as e:
            return self.err(str(e))
        # role=tool can't carry an image; attach it as a follow-up user message
        # the vision model reads next turn (ToolResult.extra_messages).
        msg = ChatMessage(
            role="user",
            content=[
                {"type": "text", "text": f"[read_file 加载的图片: {resolved.name}]"},
                block,
            ],
        )
        return ToolResult(
            content=f"已加载图片 {resolved.name}，见下一条消息。",
            structured={"path": str(resolved), "image": True, "vision": True},
            extra_messages=[msg],
        )


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "列出目录内容。返回每个条目一行，目录后跟 `/`。"
        "默认隐藏以 `.` 开头的条目；max_entries 默认 200。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要列出的目录路径。默认为 cwd (传 '.')。"},
                "include_hidden": {"type": "boolean", "description": "是否包括点文件。默认 false。"},
                "max_entries": {"type": "integer", "description": "最多返回条目数 (默认 200，上限 500)。"},
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        path_arg = args.get("path") or "."
        include_hidden = bool(args.get("include_hidden", False))
        try:
            max_entries = min(int(args.get("max_entries", 200) or 200), _MAX_LIST_ENTRIES)
        except (TypeError, ValueError):
            max_entries = min(200, _MAX_LIST_ENTRIES)
        try:
            resolved = ctx.resolve(path_arg)
        except PathDenied as e:
            return self.err(str(e))
        if not resolved.exists():
            return self.err(f"目录不存在: {resolved}")
        if not resolved.is_dir():
            return self.err(f"{resolved} 不是目录")

        try:
            entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            return self.err(f"读取目录失败: {e}")

        rendered: list[str] = []
        total = 0
        omitted = 0
        for p in entries:
            if not include_hidden and p.name.startswith("."):
                continue
            total += 1
            if total > max_entries:
                omitted += 1
                continue
            suffix = "/" if p.is_dir() else ""
            size = ""
            if p.is_file():
                try:
                    size = f"  ({p.stat().st_size} B)"
                except OSError:
                    size = ""
            rendered.append(f"{p.name}{suffix}{size}")

        body = f"# {resolved}\n" + "\n".join(rendered)
        if omitted:
            body += f"\n\n[... 还有 {omitted} 个条目未列出。提高 max_entries 可看到更多。]"
        return self.ok(body, structured={"path": str(resolved), "count": total})


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "写入 / 覆盖一个文件 (整个内容)。需要用户确认。"
        "对新文件会自动创建父目录。无大小限制。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径。"},
                "content": {"type": "string", "description": "完整的新文件内容。"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        path_arg = args.get("path")
        content = args.get("content")
        if not isinstance(path_arg, str) or not path_arg:
            return self.err("缺少必填参数 path")
        if not isinstance(content, str):
            return self.err("content 必须是字符串")
        try:
            resolved = ctx.resolve(path_arg)
        except PathDenied as e:
            return self.err(str(e))

        exists = resolved.exists()
        # Build a small diff preview for the approval prompt.
        preview = _preview_write(resolved, content) if exists else f"[新建文件] {resolved}"

        if not ctx.confirm(
            tool=self.name,
            summary=f"write_file → {resolved}  ({len(content)} 字符, {'覆盖' if exists else '新建'})",
            detail=preview,
        ):
            return self.err("用户拒绝写入。")

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as e:
            return self.err(f"写入失败: {e}")

        return self.ok(
            f"已{'覆盖' if exists else '创建'} {resolved} ({len(content)} 字符)",
            structured={"path": str(resolved), "bytes": len(content.encode('utf-8')), "created": not exists},
        )


def _preview_write(resolved: Path, new_content: str) -> str:
    """Tiny size/head preview for write_file approval."""
    try:
        old = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"(无法读取现有文件) → 新内容前 200 字符:\n{new_content[:200]}"
    head_old = old.splitlines()[:5]
    head_new = new_content.splitlines()[:5]
    return (
        f"old size = {len(old)} chars / {len(head_old)} lines (head):\n  "
        + "\n  ".join(head_old)
        + f"\n\nnew size = {len(new_content)} chars (head):\n  "
        + "\n  ".join(head_new)
    )


# ---------------------------------------------------------------------------
# str_replace
# ---------------------------------------------------------------------------

class StrReplaceTool(Tool):
    name = "str_replace"
    description = (
        "在文件中把 old_str 替换成 new_str。old_str 必须在文件中**唯一**匹配，"
        "否则会失败 — 这是 Claude Code 风格的精确编辑工具，比 write_file 安全。"
        "需要用户确认。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标文件路径。"},
                "old_str": {
                    "type": "string",
                    "description": "要替换的精确文本片段。必须在文件中唯一出现。",
                },
                "new_str": {"type": "string", "description": "替换为的新文本。"},
            },
            "required": ["path", "old_str", "new_str"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        path_arg = args.get("path")
        old_str = args.get("old_str")
        new_str = args.get("new_str")
        if not isinstance(path_arg, str) or not path_arg:
            return self.err("缺少必填参数 path")
        if not isinstance(old_str, str) or not isinstance(new_str, str):
            return self.err("old_str / new_str 必须是字符串")
        if not old_str:
            return self.err("old_str 不能为空")

        try:
            resolved = ctx.resolve(path_arg)
        except PathDenied as e:
            return self.err(str(e))
        if not resolved.exists() or resolved.is_dir():
            return self.err(f"目标文件不存在或不是文件: {resolved}")

        try:
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return self.err(f"读取失败: {e}")

        occurrences = text.count(old_str)
        if occurrences == 0:
            return self.err(
                "old_str 未在文件中找到。",
                hint="保证 old_str 与文件中的内容完全一致 (包括缩进与换行)。",
            )
        if occurrences > 1:
            return self.err(
                f"old_str 在文件中出现 {occurrences} 次，需要唯一匹配。",
                hint="把 old_str 扩展为更长的上下文片段，让它仅匹配一处。",
            )

        new_text = text.replace(old_str, new_str, 1)

        if not ctx.confirm(
            tool=self.name,
            summary=f"str_replace → {resolved}  ({len(old_str)}→{len(new_str)} 字符)",
            detail=f"- {old_str.splitlines()[0][:120]}\n+ {new_str.splitlines()[0][:120] if new_str else '(empty)'}",
        ):
            return self.err("用户拒绝写入。")

        try:
            resolved.write_text(new_text, encoding="utf-8")
        except OSError as e:
            return self.err(f"写入失败: {e}")

        diff_summary = f"已替换 1 处 (~{len(old_str)} → {len(new_str)} 字符)"
        return self.ok(diff_summary, structured={"path": str(resolved), "old_len": len(old_str), "new_len": len(new_str)})


__all__ = ["ReadFileTool", "ListDirTool", "WriteFileTool", "StrReplaceTool"]
