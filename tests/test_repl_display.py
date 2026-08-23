# tests/test_repl_display.py
"""REPL display regression tests — the artifacts users actually see.

Covers the 2026-08-17 cleanup: tool-bubble body style (indent, no
half-bar), and the AI ghost-text ASCII↔CJK boundary space.
"""

from __future__ import annotations

import io

from rich.console import Console

from modelbridge.agent.ai_completer import _apply_boundary_space
from modelbridge.agent.ui import render_tool_bubble


def _capture(fn) -> str:
    console = Console(file=io.StringIO(), width=100, force_terminal=False)
    fn(console)
    return console.file.getvalue()


# ---------------------------------------------------------------------------
# Tool bubble — compact right-aligned entry (2026-08-22 opencode-style change)
# ---------------------------------------------------------------------------

def test_tool_bubble_one_line_with_inline_short_body():
    out = _capture(lambda c: render_tool_bubble(
        c,
        tool_name="hotkey",
        args_preview="keys=['ctrl', 'c']",
        body="pyautogui 未安装。运行: pip install pyautogui\n（注意：管理员权限）",
    ))
    # Single compact line: ✓ + name + dim args + inline body (newlines folded).
    assert "✓" in out and "hotkey" in out and "keys=['ctrl', 'c']" in out
    assert "pyautogui 未安装" in out
    assert out.count("\n") <= 2  # one rendered line (+ possible wrap artifacts)
    assert "▎" not in out


def test_tool_bubble_long_body_shows_size_hint():
    out = _capture(lambda c: render_tool_bubble(
        c, tool_name="read_file", args_preview="p=a", body="x" * 2000
    ))
    assert "2000 字符" in out
    assert len(out) < 200  # compact — the body itself never prints


def test_tool_bubble_error_shows_first_line():
    out = _capture(lambda c: render_tool_bubble(
        c,
        tool_name="str_replace",
        args_preview="p=a",
        body="old_str 未在文件中找到。\n第二行不该出现",
        is_error=True,
    ))
    assert "✗" in out
    assert "old_str 未在文件中找到" in out
    assert "第二行不该出现" not in out


# ---------------------------------------------------------------------------
# Ghost-text ASCII↔CJK boundary
# ---------------------------------------------------------------------------

def test_space_inserted_between_ascii_and_cjk():
    assert _apply_boundary_space("python -m mkdocs serve", "你帮我运行") == " 你帮我运行"
    assert _apply_boundary_space("先跑起来", "mkdocs serve") == " mkdocs serve"


def test_no_space_inside_same_script():
    # 中文接中文 / ASCII 接 ASCII：不插空格（代码补全不能被污染）。
    assert _apply_boundary_space("帮我运行", "一下命令") == "一下命令"
    assert _apply_boundary_space("python -m mkd", "ocs serve") == "ocs serve"
    # 已有空白边界：不动。
    assert _apply_boundary_space("run it ", "现在") == "现在"
    # 空侧：原样。
    assert _apply_boundary_space("", "建议") == "建议"
    assert _apply_boundary_space("abc", "") == ""
