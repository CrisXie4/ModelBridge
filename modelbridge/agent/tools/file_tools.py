"""File-system tools.

Read-only tools (``read_file``, ``list_dir``) just enforce :class:`PathPolicy`.
Mutating tools (``write_file``, ``str_replace``) additionally require
``ctx.confirm(...)`` before touching anything.

All tools cap output size so a 10 MB log file can't blow up the context
window — they return a clear "truncated" notice when that happens.
"""

from __future__ import annotations

import os
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
        # gets a clear note instead of decoded bytes. Videos (mp4/webm/…)
        # follow the same pattern via ``video_url`` blocks for models that
        # declare capabilities.video.
        if images.is_image_path(resolved.name):
            return self._read_image(resolved, ctx)
        if images.is_video_path(resolved.name):
            return self._read_video(resolved, ctx)

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

    def _read_video(self, resolved: Path, ctx: AgentContext) -> ToolResult:
        caps_video = getattr(ctx, "model_is_video", False)
        caps_vision = getattr(ctx, "model_is_vision", False)
        if not caps_vision:
            return self.ok(
                f"[{resolved.name} 是视频文件，当前模型不支持 vision，无法识别。"
                f"换一个 capabilities.vision=true 的模型再读。]",
                structured={"path": str(resolved), "video": True, "vision": False},
            )
        if not caps_video:
            return self.ok(
                f"[{resolved.name} 是视频文件，当前模型支持图片但不支持视频输入"
                "（需要 capabilities.video=true 的模型，如 GLM-4V / qwen-vl 系列）。]",
                structured={"path": str(resolved), "video": True, "video_ok": False},
            )
        try:
            block = images.video_block_from_path(str(resolved))
        except images.ImageError as e:
            return self.err(str(e))
        msg = ChatMessage(
            role="user",
            content=[
                {"type": "text", "text": f"[read_file 加载的视频: {resolved.name}]"},
                block,
            ],
        )
        return ToolResult(
            content=f"已加载视频 {resolved.name}，见下一条消息。",
            structured={"path": str(resolved), "video": True, "video_ok": True},
            extra_messages=[msg],
        )

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
            # 与 str_replace 共享一个 "always" 决定：批准一次文件写，
            # 本会话内两种写法都不再问。
            group="file_write",
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


# ---------------------------------------------------------------------------
# grep — content search (read-only)
# ---------------------------------------------------------------------------

#: Dirs never worth searching (build output / deps / VCS internals).
_GREP_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".modelbridge", ".idea", ".vs",
}
_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024  # skip huge blobs; grep targets source


class GrepTool(Tool):
    name = "grep"
    description = (
        "在项目文件内容里按正则搜索，返回 ``文件:行号: 行文本`` 的匹配列表。"
        "只读工具；自动跳过 .git/node_modules/__pycache__ 等目录和二进制文件。"
        "找代码、找符号、找配置项都用它，定位后用 read_file 精读。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "正则表达式（Python re 语法）。",
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录（默认当前目录）。超出 allowed_dirs 会被拒绝。",
                },
                "include": {
                    "type": "string",
                    "description": "只搜索匹配该通配符的文件，如 '*.py'。",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "忽略大小写（默认 false）。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回匹配数（默认 100，上限 300）。",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        import fnmatch
        import re

        pattern_arg = args.get("pattern")
        if not isinstance(pattern_arg, str) or not pattern_arg:
            return self.err("缺少必填参数 pattern")
        try:
            flags = re.IGNORECASE if args.get("ignore_case") else 0
            rx = re.compile(pattern_arg, flags)
        except re.error as e:
            return self.err(f"无效的正则: {e}")

        try:
            max_results = min(int(args.get("max_results", 100) or 100), 300)
        except (TypeError, ValueError):
            max_results = 100
        include = args.get("include") or None
        root_arg = args.get("path") or "."
        try:
            root = ctx.resolve(root_arg)
        except PathDenied as e:
            return self.err(str(e))
        if not root.exists():
            return self.err(f"目录不存在: {root}")
        if not root.is_dir():
            return self.err(f"{root} 不是目录")

        matches: list[str] = []
        files_hit = 0
        files_scanned = 0
        truncated = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _GREP_SKIP_DIRS)
            for fname in sorted(filenames):
                if fname.startswith(".") and fname != ".env.example":
                    continue
                if include and not fnmatch.fnmatch(fname, include):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    if fpath.stat().st_size > _GREP_MAX_FILE_BYTES:
                        continue
                    data = fpath.read_bytes()
                except OSError:
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # non-utf8 — grep targets source text
                if "\x00" in text:
                    continue  # NUL byte = binary even when technically utf-8
                files_scanned += 1
                hit_in_file = False
                for lineno, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        if not hit_in_file:
                            files_hit += 1
                            hit_in_file = True
                        if len(matches) < max_results:
                            rel = fpath.relative_to(root) if fpath.is_relative_to(root) else fpath
                            preview = line.strip()
                            if len(preview) > 200:
                                preview = preview[:200] + "…"
                            matches.append(f"{rel}:{lineno}: {preview}")
                        else:
                            truncated = True
                            break
                if truncated:
                    break
            if truncated:
                break

        if not matches:
            return self.ok(
                f"在 {root} 下扫描 {files_scanned} 个文件，没有匹配 {pattern_arg!r}。",
                structured={"pattern": pattern_arg, "files_scanned": files_scanned},
            )
        body = "\n".join(matches)
        if truncated:
            body += f"\n\n[超过 {max_results} 处匹配，已截断 — 收窄 pattern/路径或提高 max_results]"
        return self.ok(
            f"{files_hit} 个文件命中（扫描 {files_scanned} 个）：\n\n{body}",
            structured={"pattern": pattern_arg, "files_hit": files_hit},
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
            # 与 write_file 共享 "always"（见 WriteFileTool）。
            group="file_write",
        ):
            return self.err("用户拒绝写入。")

        try:
            resolved.write_text(new_text, encoding="utf-8")
        except OSError as e:
            return self.err(f"写入失败: {e}")

        diff_summary = f"已替换 1 处 (~{len(old_str)} → {len(new_str)} 字符)"
        return self.ok(diff_summary, structured={"path": str(resolved), "old_len": len(old_str), "new_len": len(new_str)})


__all__ = ["ReadFileTool", "ListDirTool", "WriteFileTool", "StrReplaceTool", "GrepTool"]
