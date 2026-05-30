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
