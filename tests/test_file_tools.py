"""Unit tests for the agent file tools.

Exercises ``read_file`` / ``list_dir`` / ``write_file`` / ``str_replace``
against a real :class:`PathPolicy` rooted at a ``tmp_path`` sandbox, so the
path allowlist, the sensitive-file blocklist, and the confirm-before-write
contract are all covered without touching the user's real filesystem.
"""

from __future__ import annotations

from modelbridge.agent.context import AgentContext, auto_no, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.file_tools import (
    ListDirTool,
    ReadFileTool,
    StrReplaceTool,
    WriteFileTool,
)


def _ctx(tmp_path, *, approve=auto_yes, blocked=None):
    policy = PathPolicy(
        allowed_dirs=[tmp_path.resolve()],
        blocked_patterns=blocked if blocked is not None else [".env", "id_rsa", "*.key"],
    )
    return AgentContext(policy=policy, cwd=tmp_path.resolve(), approve=approve)


# --- read_file --------------------------------------------------------------

def test_read_file_ok(tmp_path):
    (tmp_path / "hello.txt").write_text("你好 world", encoding="utf-8")
    res = ReadFileTool().execute({"path": "hello.txt"}, _ctx(tmp_path))
    assert not res.is_error
    assert res.content == "你好 world"


def test_read_missing_file_errors(tmp_path):
    res = ReadFileTool().execute({"path": "nope.txt"}, _ctx(tmp_path))
    assert res.is_error
    assert "不存在" in res.content


def test_read_directory_errors(tmp_path):
    (tmp_path / "sub").mkdir()
    res = ReadFileTool().execute({"path": "sub"}, _ctx(tmp_path))
    assert res.is_error
    assert "list_dir" in res.content


def test_read_outside_allowed_dir_denied(tmp_path):
    # An absolute path that resolves outside the sandbox must be denied.
    outside = tmp_path.parent / "evil.txt"
    outside.write_text("secret", encoding="utf-8")
    res = ReadFileTool().execute({"path": str(outside)}, _ctx(tmp_path))
    assert res.is_error


def test_read_blocked_sensitive_file_denied(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=sk-xxx", encoding="utf-8")
    res = ReadFileTool().execute({"path": ".env"}, _ctx(tmp_path))
    assert res.is_error
    assert "敏感" in res.content or "拒绝" in res.content


def test_from_config_blocks_secrets_beyond_config_literals(tmp_path, monkeypatch):
    """The agent REPL builds its policy via PathPolicy.from_config, whose
    block list only has a few literals (.env / id_rsa / …). Regression for the
    leak where read_file/write_file would happily read .env.local / *.pem /
    .npmrc etc. — the policy must union the scanner's glob baseline so all
    read/write paths refuse the same secrets that @file and `mbridge edit` do.
    """
    import pytest

    import modelbridge.agent.security as sec
    from modelbridge.models import AppConfig

    cfg = AppConfig()  # default security.block_sensitive_files = no globs
    monkeypatch.setattr(sec, "load_app_config", lambda: cfg)
    policy = sec.PathPolicy.from_config(extra_cwd=tmp_path.resolve())

    for name in (
        ".env.local",
        ".env.production",
        "server.pem",
        "deploy.key",
        ".npmrc",
        ".netrc",
        "credentials.json",
    ):
        (tmp_path / name).write_text("secret", encoding="utf-8")
        with pytest.raises(sec.PathDenied):
            policy.resolve(name)

    # A normal source file is still allowed.
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    assert policy.resolve("main.py").name == "main.py"


# --- list_dir ---------------------------------------------------------------

def test_list_dir_hides_dotfiles_by_default(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / ".hidden").write_text("h", encoding="utf-8")
    res = ListDirTool().execute({"path": "."}, _ctx(tmp_path))
    assert not res.is_error
    assert "a.txt" in res.content and "b.txt" in res.content
    assert ".hidden" not in res.content


def test_list_dir_include_hidden(tmp_path):
    (tmp_path / ".hidden").write_text("h", encoding="utf-8")
    res = ListDirTool().execute(
        {"path": ".", "include_hidden": True}, _ctx(tmp_path)
    )
    assert ".hidden" in res.content


# --- write_file -------------------------------------------------------------

def test_write_file_creates_with_approval(tmp_path):
    res = WriteFileTool().execute(
        {"path": "out/new.txt", "content": "data"}, _ctx(tmp_path, approve=auto_yes)
    )
    assert not res.is_error
    assert (tmp_path / "out" / "new.txt").read_text(encoding="utf-8") == "data"


def test_write_file_denied_when_user_says_no(tmp_path):
    res = WriteFileTool().execute(
        {"path": "new.txt", "content": "data"}, _ctx(tmp_path, approve=auto_no)
    )
    assert res.is_error
    assert not (tmp_path / "new.txt").exists()


# --- str_replace ------------------------------------------------------------

def test_str_replace_unique_match(tmp_path):
    (tmp_path / "f.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "b = 2", "new_str": "b = 3"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a = 1\nb = 3\n"


def test_str_replace_no_match_errors(tmp_path):
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "zzz", "new_str": "q"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert res.is_error
    assert "未在文件中找到" in res.content


def test_str_replace_ambiguous_match_errors(tmp_path):
    (tmp_path / "f.py").write_text("x\nx\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "x", "new_str": "y"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert res.is_error
    assert "2 次" in res.content
    # File must be left untouched on an ambiguous match.
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "x\nx\n"


def test_list_dir_non_numeric_max_entries_falls_back(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    res = ListDirTool().execute({"path": ".", "max_entries": "abc"}, _ctx(tmp_path))
    assert not res.is_error
    assert "a.txt" in res.content


# --- 行尾保真（write_file / str_replace 不改行尾） ---------------------------
# 断言全部基于 read_bytes()：在 Windows 上 os.linesep == "\r\n"，修复前
# 默认 write_text 会把 \n 翻译成 CRLF，这些测试会真实失败。

def test_str_replace_preserves_lf_line_endings(tmp_path):
    """LF-only 文件做一次 str_replace 后必须仍是 LF（U1 主 bug）。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"a = 1\nb = 2\n")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "b = 2", "new_str": "b = 3"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"a = 1\nb = 3\n"


def test_str_replace_preserves_crlf_line_endings(tmp_path):
    """CRLF 文件编辑后仍全部是 CRLF，且用 \\n 构造的 old_str 能命中。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"a = 1\r\nb = 2\r\n")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "b = 2", "new_str": "b = 3"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_str_replace_multi_line_replacement_inherits_file_eol(tmp_path):
    """多行 new_str 写进 CRLF 文件时新行也应是 CRLF。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"def f():\r\n    pass\r\n")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "def f():", "new_str": "def f():\n    x = 1"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"def f():\r\n    x = 1\r\n    pass\r\n"


def test_str_replace_crlf_old_str_from_read_file_output(tmp_path):
    """read_file 返回原样字节（含 \\r\\n），模型照抄的 CRLF old_str 也要能命中。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"a = 1\r\nb = 2\r\n")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "b = 2\r\n", "new_str": "b = 3\r\n"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_write_file_new_file_keeps_content_verbatim(tmp_path):
    """新建文件按内容原样写：\\n 保持 \\n，不做 os.linesep 翻译。"""
    res = WriteFileTool().execute(
        {"path": "new.py", "content": "x = 1\ny = 2\n"}, _ctx(tmp_path, approve=auto_yes)
    )
    assert not res.is_error
    assert (tmp_path / "new.py").read_bytes() == b"x = 1\ny = 2\n"


def test_write_file_overwrite_inherits_crlf(tmp_path):
    """覆盖 CRLF 文件时，新内容（\\n）应转成文件原有的 CRLF。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"old = 1\r\nold = 2\r\n")
    res = WriteFileTool().execute(
        {"path": "f.py", "content": "new = 1\nnew = 2\n"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"new = 1\r\nnew = 2\r\n"


def test_write_file_overwrite_inherits_lf(tmp_path):
    """覆盖 LF-only 文件时内容里偶发的 CRLF 也应归一化成 LF，整体不改风格。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"old = 1\nold = 2\n")
    res = WriteFileTool().execute(
        {"path": "f.py", "content": "new = 1\r\nnew = 2\r\n"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"new = 1\nnew = 2\n"


# --- 容错匹配阶梯（U7）：exact 优先、每级强制唯一、按实际命中文本写回 -------

def test_str_replace_exact_path_unchanged(tmp_path):
    """exact 命中行为与纯精确匹配逐字节一致。"""
    p = tmp_path / "f.py"
    p.write_bytes(b"a = (1, 2)\nb = 2\n")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "a = (1, 2)", "new_str": "a = (3, 4)"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == b"a = (3, 4)\nb = 2\n"


def test_str_replace_curly_quote_fallback(tmp_path):
    """文件里是纯弯引号、模型给直引号 → 降级命中实际弯引号片段，写回时
    new_str 的直引号按开/闭交替映射回弯引号（风格不漂移）。"""
    p = tmp_path / "f.md"
    p.write_text("msg = “你好世界”\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.md", "old_str": 'msg = "你好世界"', "new_str": 'msg = "再见世界"'},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == "msg = “再见世界”\n"


def test_str_replace_curly_apostrophe_letter_sandwich(tmp_path):
    """单引号字母夹注（英文所有格）映射为 ’ 而非开引号。"""
    p = tmp_path / "f.md"
    p.write_text("word = ‘don’t’\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.md", "old_str": "word = 'don't'", "new_str": "word = 'won't'"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == "word = ‘won’t’\n"


def test_str_replace_mixed_quote_span_writes_new_verbatim(tmp_path):
    """直弯混排片段：风格归属不明，new_str 原样写回（不做映射）。"""
    p = tmp_path / "f.md"
    p.write_text('t = "直"与“弯”混排\n', encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.md", "old_str": 't = "直"与"弯"混排', "new_str": 't = "新"与"弯"混排'},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == 't = "新"与"弯"混排\n'


def test_str_replace_escape_fallback(tmp_path):
    """模型把换行写成字面 \\n → 转义归一级命中；new_str 同样反转义。"""
    p = tmp_path / "f.txt"
    p.write_text("line-a\nline-b\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.txt", "old_str": "line-a\\nline-b", "new_str": "line-x\\nline-y"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == "line-x\nline-y\n"


def test_str_replace_line_trimmed_fallback(tmp_path):
    """模型 old_str 每行缺前导空格 → 整行 trim 级命中实际带缩进的块。"""
    p = tmp_path / "f.py"
    p.write_text("def f():\n    foo = 1\n    bar = 2\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "foo = 1\nbar = 2", "new_str": "    foo = 10\n    bar = 20"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == "def f():\n    foo = 10\n    bar = 20\n"


def test_str_replace_indent_flexible_fallback(tmp_path):
    """文件块整体缩进比 old_str 深 → 缩进宽松级（剥公共缩进后相等）命中。"""
    p = tmp_path / "f.py"
    p.write_text("class A:\n        x = 1\n        y = 2\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "    x = 1\n    y = 2", "new_str": "    x = 9\n    y = 8"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_text(encoding="utf-8") == "class A:\n    x = 9\n    y = 8\n"


def test_str_replace_fuzzy_level_ambiguous_errors(tmp_path):
    """容错级（exact 无命中、整行 trim 命中 2 处）判歧义并标明级别，不落盘。"""
    p = tmp_path / "f.py"
    p.write_text("  x = 1\n  x = 1\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "x = 1  ", "new_str": "x = 2"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert res.is_error
    assert "2 次" in res.content
    assert "line_trimmed" in res.content  # 报错标明命中的容错级别
    assert p.read_text(encoding="utf-8") == "  x = 1\n  x = 1\n"


def test_str_replace_ladder_not_found_hint(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("real content\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "完全不存在的片段", "new_str": "x"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert res.is_error
    assert "未在文件中找到" in res.content


def test_str_replace_curly_fallback_in_crlf_file(tmp_path):
    """CRLF 文件 + 纯弯引号降级：命中且行尾仍保真（与 U1 协同）。"""
    p = tmp_path / "f.md"
    p.write_bytes("title = “前引后”\r\nok\r\n".encode("utf-8"))
    res = StrReplaceTool().execute(
        {"path": "f.md", "old_str": 'title = "前引后"', "new_str": 'title = "新引旧"'},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error
    assert p.read_bytes() == "title = “新引旧”\r\nok\r\n".encode("utf-8")


# --- 文件读取账本（U8）：陈旧拒编 + 重复读返回短 stub ------------------------

def test_read_file_repeat_returns_stub(tmp_path):
    """同一文件未变化时重复读取返回短 stub，不再重发全文。"""
    p = tmp_path / "f.txt"
    p.write_text("long content\n" * 100, encoding="utf-8")
    ctx = _ctx(tmp_path)
    r1 = ReadFileTool().execute({"path": "f.txt"}, ctx)
    assert not r1.is_error
    assert "long content" in r1.content
    r2 = ReadFileTool().execute({"path": "f.txt"}, ctx)
    assert not r2.is_error
    assert "未变化" in r2.content
    assert "long content" not in r2.content  # 不重发全文
    assert r2.structured["unchanged"] is True


def test_read_file_after_external_change_resends_full(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("v1\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    ReadFileTool().execute({"path": "f.txt"}, ctx)
    p.write_text("v2 changed content\n", encoding="utf-8")  # 外部改动
    r2 = ReadFileTool().execute({"path": "f.txt"}, ctx)
    assert not r2.is_error
    assert "v2 changed content" in r2.content  # 变了 → 全文重发并重新记账
    r3 = ReadFileTool().execute({"path": "f.txt"}, ctx)
    assert "未变化" in r3.content


def test_str_replace_stale_read_rejected(tmp_path):
    """读过但文件被外部（用户/linter）改动 → 拒编并请重读。"""
    p = tmp_path / "f.py"
    p.write_text("a = 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path, approve=auto_yes)
    assert not ReadFileTool().execute({"path": "f.py"}, ctx).is_error
    p.write_text("a = 999\n", encoding="utf-8")  # 外部改动
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "a = 999", "new_str": "a = 3"},
        ctx,
    )
    assert res.is_error
    assert "外部修改" in res.content
    assert p.read_text(encoding="utf-8") == "a = 999\n"  # 未落盘
    # 重新 read_file（读到新内容并刷新账本）后即可正常编辑。
    assert not ReadFileTool().execute({"path": "f.py"}, ctx).is_error
    res2 = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "a = 999", "new_str": "a = 3"},
        ctx,
    )
    assert not res2.is_error
    assert p.read_text(encoding="utf-8") == "a = 3\n"


def test_str_replace_unread_file_unaffected(tmp_path):
    """未读过的文件行为完全不变（账本纯加防护）。"""
    p = tmp_path / "f.py"
    p.write_text("a = 1\n", encoding="utf-8")
    res = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "a = 1", "new_str": "a = 2"},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not res.is_error


def test_write_file_stale_read_rejected(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("old\n", encoding="utf-8")
    ctx = _ctx(tmp_path, approve=auto_yes)
    assert not ReadFileTool().execute({"path": "f.py"}, ctx).is_error
    p.write_text("hacked\n", encoding="utf-8")  # 外部改动
    res = WriteFileTool().execute({"path": "f.py", "content": "new\n"}, ctx)
    assert res.is_error
    assert "外部修改" in res.content
    assert p.read_text(encoding="utf-8") == "hacked\n"  # 未被覆盖


def test_consecutive_tool_writes_not_flagged_stale(tmp_path):
    """自己的写入刷新账本：read → str_replace → str_replace 连续编辑不误报。"""
    p = tmp_path / "f.py"
    p.write_text("a = 1\nb = 2\n", encoding="utf-8")
    ctx = _ctx(tmp_path, approve=auto_yes)
    assert not ReadFileTool().execute({"path": "f.py"}, ctx).is_error
    r1 = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "a = 1", "new_str": "a = 10"}, ctx)
    r2 = StrReplaceTool().execute(
        {"path": "f.py", "old_str": "b = 2", "new_str": "b = 20"}, ctx)
    assert not r1.is_error and not r2.is_error
    assert p.read_text(encoding="utf-8") == "a = 10\nb = 20\n"
