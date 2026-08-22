# tests/test_default_prompts.py
"""Default system-prompt behaviour: bash/browser conditional blocks and the
 strengthened working-loop content. The tool list format is load-bearing
 (models parse tool names/params from it), so these tests pin it.
"""

from __future__ import annotations

from modelbridge.cli import _default_system_prompt
from modelbridge.prompt.defaults import DEFAULT_SYSTEM_MD


def test_repl_prompt_tools_block_is_stable():
    """Tool inventory + signatures must stay byte-stable — the model relies
    on this list to emit correct tool calls."""
    for allow_bash in (True, False):
        text = _default_system_prompt(allow_bash=allow_bash)
        for line in (
            "- read_file(path): 读取项目内文件 (path 相对于工作目录)。\n",
            "- list_dir(path): 列出目录条目。\n",
            "- write_file(path, content): 覆盖/创建文件 (每次会请求用户确认)。\n",
            "- str_replace(path, old_str, new_str): 精确替换 (要求 old_str 在文件中唯一出现)。\n",
        ):
            assert line in text
        assert "str_replace" in text


def test_repl_prompt_bash_line_follows_flag():
    with_bash = _default_system_prompt(allow_bash=True)
    without = _default_system_prompt(allow_bash=False)
    assert "run_bash(command)" in with_bash
    assert "run_bash" not in without


def test_repl_prompt_has_agent_working_loop():
    """The strengthened prompt must demand multi-step progress + verify."""
    text = _default_system_prompt(allow_bash=False)
    for keyword in ("多步推进", "改后验证", "最小改动", "失败换路", "先读后写"):
        assert keyword in text


def test_default_system_md_has_task_execution_section():
    assert "## 任务执行" in DEFAULT_SYSTEM_MD
    for keyword in ("多步推进", "改后验证", "不轻言放弃", "假设要明说"):
        assert keyword in DEFAULT_SYSTEM_MD
    # 必须遵守 section kept intact (confirmation-before-edit etc.).
    assert "**修改文件需要确认**" in DEFAULT_SYSTEM_MD
