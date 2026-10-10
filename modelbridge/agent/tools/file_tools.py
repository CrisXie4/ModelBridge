"""File-system tools.

Read-only tools (``read_file``, ``list_dir``) just enforce :class:`PathPolicy`.
Mutating tools (``write_file``, ``str_replace``) additionally require
``ctx.confirm(...)`` before touching anything.

All tools cap output size so a 10 MB log file can't blow up the context
window — they return a clear "truncated" notice when that happens.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ... import images
from ...editor.line_endings import (
    apply_line_endings,
    detect_line_endings,
    normalize_to_lf,
    read_text_raw,
)
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

        # U8 账本：同一文件与上次读取时 (mtime, size) 完全一致 → 返回短
        # stub 而不是重发全文（重复读取是上下文的大头浪费）。stub 指回上次
        # 结果，模型无需重复内容即可继续。
        state = AgentContext.file_fingerprint(resolved)
        if state is not None and ctx.file_read_state(resolved) == state:
            return self.ok(
                f"[{resolved} 自上次 read_file 后未变化 (mtime/size 相同) — "
                "请直接使用上次读取的内容，无需重复获取全文。]",
                structured={
                    "path": str(resolved), "unchanged": True, "bytes": state[1],
                },
            )

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

        # 文本读取成功 → 记账（图片/视频走独立消费路径，不记账）。
        ctx.note_file_read(resolved)

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
        # U8 账本防陈旧（仅覆盖已有文件时）：读过但已被外部改动 → 拒绝覆盖。
        if exists and ctx.is_externally_modified(resolved):
            return self.err(
                "文件在最近一次 read_file 之后已被外部修改（大小/mtime 变化）。",
                hint="先重新 read_file 获取最新内容，再整体覆盖。",
            )
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
            # 行尾保真：覆盖已有文件时沿用其原行尾（多数派判定），新文件按
            # 模型给的内容原样写。所有写盘都 newline=""——默认 write_text
            # 会把 \n 翻译成 os.linesep，在 Windows 上把 LF-only 文件整体
            # 改写成 CRLF（制造全文件 git diff 噪声）。
            data = content
            if exists:
                try:
                    eol = detect_line_endings(read_text_raw(resolved))
                except (OSError, UnicodeDecodeError):
                    eol = None  # 读不出原行尾（不可读/非 UTF-8）→ 按原样写
                if eol is not None:
                    data = apply_line_endings(content, eol)
            resolved.write_text(data, encoding="utf-8", newline="")
        except OSError as e:
            return self.err(f"写入失败: {e}")
        # 写入成功 → 刷新账本：自己的写入不算“外部改动”，后续编辑照常。
        ctx.note_file_read(resolved)

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
# str_replace —— 容错匹配阶梯（U7）
# ---------------------------------------------------------------------------

class _AmbiguousMatch(Exception):
    """某一级匹配到 ≥2 处 —— 不再降级，直接判歧义（对齐 ZCode findEditMatch）。"""

    def __init__(self, strategy: str, count: int) -> None:
        super().__init__(f"{strategy}: {count} matches")
        self.strategy = strategy
        self.count = count


_CURLY_DOUBLE = ("“", "”")
_CURLY_SINGLE = ("‘", "’")
_UNESCAPE_MAP = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "`": "`", "\\": "\\", "$": "$"}


def _normalize_quotes(v: str) -> str:
    """弯引号 → 直引号（长度不变，索引可 1:1 映射回原文）。"""
    return (
        v.replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
    )


def _unescape_visible(v: str) -> str:
    r"""\n \t \r \" \' \` \\ \$ 等可见转义 → 真实字符（对齐 ZCode
    unescapeVisibleCharacters）—— 模型把换行写成字面 \n 时也能命中。"""
    return re.sub(
        r"\\([ntr\"'`\\$])",
        lambda m: _UNESCAPE_MAP[m.group(1)],
        v,
    )


def _find_spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """needle 在 haystack 中的所有出现区间。"""
    if not needle:
        return []
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        i = haystack.find(needle, pos)
        if i < 0:
            break
        spans.append((i, i + len(needle)))
        pos = i + max(len(needle), 1)
    return spans


def _needle_lines(old_lf: str) -> list[str]:
    lines = old_lf.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # 尾随换行不参与整行匹配
    return lines


def _line_trimmed_candidates(text: str, old_lf: str) -> list[str]:
    """整行 trim 匹配：行数相同、逐行 strip() 相等的块（返回实际块文本）。"""
    lines = text.split("\n")
    needle = [line.strip() for line in _needle_lines(old_lf)]
    n = len(needle)
    if n == 0:
        return []
    out: list[str] = []
    for i in range(0, len(lines) - n + 1):
        block = lines[i:i + n]
        if all(block[j].strip() == needle[j] for j in range(n)):
            out.append("\n".join(block))
    return out


def _strip_common_indent(lines: list[str]) -> list[str]:
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return list(lines)
    cut = min(len(line) - len(line.lstrip(" \t")) for line in non_empty)
    return [line if not line.strip() else line[cut:] for line in lines]


def _indent_flexible_candidates(text: str, old_lf: str) -> list[str]:
    """缩进宽松匹配：剥掉各自行首公共缩进后逐行相等的块（≥2 行才启用）。"""
    lines = text.split("\n")
    needle = _strip_common_indent(_needle_lines(old_lf))
    n = len(needle)
    if n < 2:
        return []
    target = "\n".join(needle)
    out: list[str] = []
    for i in range(0, len(lines) - n + 1):
        block = lines[i:i + n]
        if "\n".join(_strip_common_indent(block)) == target:
            out.append("\n".join(block))
    return out


def _apply_curly_style(actual_old: str, new_text: str) -> str:
    """弯引号风格保真（仅在"纯弯"片段上生效）：实际命中文本含弯引号且**不含**
    对应直引号时，把 new_text 里的直引号映射回弯引号——双引号按开/闭交替，
    单引号字母夹注（don't → don’t）优先、其余交替。混排片段（同种引号直弯
    并存）不做映射，new_text 原样写回——风格归属不明时以模型字面为准。"""
    has_curly_d = any(c in actual_old for c in _CURLY_DOUBLE)
    has_straight_d = '"' in actual_old
    has_curly_s = any(c in actual_old for c in _CURLY_SINGLE)
    has_straight_s = "'" in actual_old
    chars = list(new_text)
    d_toggle = True   # True → 下一个 " 是开引号
    s_toggle = True
    for i, ch in enumerate(chars):
        if ch == '"' and has_curly_d and not has_straight_d:
            chars[i] = "“" if d_toggle else "”"
            d_toggle = not d_toggle
        elif ch == "'" and has_curly_s and not has_straight_s:
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            prev = chars[i - 1] if i else ""
            if prev.isalpha() and nxt.isalpha():
                chars[i] = "’"  # don't → don’t 的字母夹注
            else:
                chars[i] = "‘" if s_toggle else "’"
                s_toggle = not s_toggle
    return "".join(chars)


def _match_ladder(
    text: str, old_lf: str, new_lf: str,
) -> tuple[str, str, str] | None:
    """exact 优先的容错匹配阶梯（对齐 ZCode edit-matchers 的 strategies）。

    返回 ``(级别, 实际命中文本, 实际替换文本)``；全部未命中返回 None。
    每级命中 ≥2 处抛 :class:`_AmbiguousMatch`（判歧义，不再降级）。
    exact 级命中即原样返回 —— 行为与纯精确匹配逐字节一致，阶梯只是
    纯加宽成功面：模型抄写有出入（弯引号、转义、缩进）时降级命中，
    替换按"实际命中的文本"写回。
    """
    # 1) exact
    spans = _find_spans(text, old_lf)
    if len(spans) > 1:
        raise _AmbiguousMatch("exact", len(spans))
    if len(spans) == 1:
        return ("exact", old_lf, new_lf)

    # 2) 弯引号归一（长度不变，span 1:1 映射回原文）
    q_spans = _find_spans(_normalize_quotes(text), _normalize_quotes(old_lf))
    if len(q_spans) > 1:
        raise _AmbiguousMatch("quote_normalized", len(q_spans))
    if q_spans:
        s, e = q_spans[0]
        actual = text[s:e]
        return ("quote_normalized", actual, _apply_curly_style(actual, new_lf))

    # 3) 可见转义归一（\n 等字面转义 → 真实字符；替换文本同样反转义）
    unescaped = _unescape_visible(old_lf)
    if unescaped != old_lf:
        spans = _find_spans(text, unescaped)
        if len(spans) > 1:
            raise _AmbiguousMatch("escape_normalized", len(spans))
        if spans:
            return ("escape_normalized", unescaped, _unescape_visible(new_lf))

    # 4) 整行 trim
    cands = _line_trimmed_candidates(text, old_lf)
    if len(cands) > 1:
        raise _AmbiguousMatch("line_trimmed", len(cands))
    if cands:
        return ("line_trimmed", cands[0], new_lf)

    # 5) 缩进宽松（≥2 行）
    cands = _indent_flexible_candidates(text, old_lf)
    if len(cands) > 1:
        raise _AmbiguousMatch("indentation_flexible", len(cands))
    if cands:
        return ("indentation_flexible", cands[0], new_lf)

    return None


class StrReplaceTool(Tool):
    name = "str_replace"
    description = (
        "在文件中把 old_str 替换成 new_str。old_str 必须在文件中**唯一**匹配，"
        "否则会失败 — 这是 Claude Code 风格的精确编辑工具，比 write_file 安全。"
        "精确匹配失败时按容错阶梯降级（弯引号/转义/整行 trim/缩进宽松），"
        "每级仍强制唯一命中。需要用户确认。"
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

        # U8 账本防陈旧：读过但已被外部（用户/linter）改动 → 拒绝并请重读。
        if ctx.is_externally_modified(resolved):
            return self.err(
                "文件在最近一次 read_file 之后已被外部修改（大小/mtime 变化）。",
                hint="先重新 read_file 获取最新内容，再基于新内容做替换。",
            )

        try:
            # 原样读入（不折叠行尾）；匹配在 LF 归一化副本上进行 —— 模型给的
            # old_str/new_str 可能带 \n（自己构造）也可能带 \r\n（从 read_file
            # 的原样输出复制），归一化后两种都能命中同一处。
            raw = read_text_raw(resolved)
        except (OSError, UnicodeDecodeError) as e:
            return self.err(f"读取失败: {e}")

        text = normalize_to_lf(raw)
        old_lf = normalize_to_lf(old_str)
        new_lf = normalize_to_lf(new_str)

        # exact 优先的容错匹配阶梯（U7）：每级强制唯一命中，非 exact 级按
        # "实际命中的文本"写回 —— old_str 只是定位器，替换不引入风格漂移。
        try:
            matched = _match_ladder(text, old_lf, new_lf)
        except _AmbiguousMatch as e:
            return self.err(
                f"old_str 在文件中出现 {e.count} 次，需要唯一匹配"
                f"（匹配级别：{e.strategy}）。",
                hint="把 old_str 扩展为更长的上下文片段，让它仅匹配一处。",
            )
        if matched is None:
            return self.err(
                "old_str 未在文件中找到。",
                hint=(
                    "保证 old_str 与文件中的内容完全一致 (包括缩进与换行)；"
                    "已自动尝试弯引号/转义/整行 trim/缩进宽松降级仍无命中。"
                ),
            )
        _strategy, actual_old, effective_new = matched

        # 写回时沿用文件原行尾（多数派判定），且 newline="" 禁止 os.linesep
        # 翻译 —— 否则 Windows 会把 LF-only 文件整体改写成 CRLF。
        new_text = apply_line_endings(
            text.replace(actual_old, effective_new, 1), detect_line_endings(raw)
        )

        if not ctx.confirm(
            tool=self.name,
            summary=f"str_replace → {resolved}  ({len(old_str)}→{len(new_str)} 字符)",
            detail=f"- {old_str.splitlines()[0][:120]}\n+ {new_str.splitlines()[0][:120] if new_str else '(empty)'}",
            # 与 write_file 共享 "always"（见 WriteFileTool）。
            group="file_write",
        ):
            return self.err("用户拒绝写入。")

        try:
            resolved.write_text(new_text, encoding="utf-8", newline="")
        except OSError as e:
            return self.err(f"写入失败: {e}")
        # 写入成功 → 刷新账本：自己的写入不算“外部改动”，连续编辑照常。
        ctx.note_file_read(resolved)

        diff_summary = f"已替换 1 处 (~{len(old_str)} → {len(new_str)} 字符)"
        return self.ok(diff_summary, structured={"path": str(resolved), "old_len": len(old_str), "new_len": len(new_str)})


__all__ = ["ReadFileTool", "ListDirTool", "WriteFileTool", "StrReplaceTool", "GrepTool"]
