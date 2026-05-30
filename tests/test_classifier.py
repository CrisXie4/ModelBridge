"""Unit tests for the keyword/heuristic task classifier.

``modelbridge.router.classifier.classify_task`` is the deterministic
fallback classifier (the LLM path lives in ``llm_classifier``). These tests
pin down the routing levels for representative prompts plus the structural
bumps (code fence, file globs) and the caller-supplied hints.
"""

from __future__ import annotations

from modelbridge.models import ModelLevel
from modelbridge.router.classifier import classify_task


def test_empty_prompt_is_unknown_tiny():
    p = classify_task("")
    assert p.task_type == "unknown"
    assert p.recommended_level == ModelLevel.TINY


def test_security_prompt_is_expert_high_risk():
    p = classify_task("检查这个项目有没有安全漏洞")
    assert p.task_type == "security_review"
    assert p.recommended_level == ModelLevel.EXPERT
    assert p.risk_level == "high"


def test_architecture_prompt_is_expert():
    p = classify_task("分析整个项目架构并提出重构建议")
    assert p.task_type == "architecture"
    assert p.recommended_level == ModelLevel.EXPERT


def test_explain_prompt_is_cheap():
    p = classify_task("什么是 Python 的 list？")
    assert p.task_type == "explain"
    assert p.recommended_level == ModelLevel.CHEAP


def test_code_generate_prompt_is_coder():
    p = classify_task("帮我写一个 FastAPI hello world")
    assert p.task_type == "code_generate"
    assert p.recommended_level == ModelLevel.CODER


def test_mcp_prompt_is_agent():
    p = classify_task("使用 MCP 工具读取 GitHub issue 并修复")
    assert p.recommended_level == ModelLevel.AGENT


def test_code_fence_bumps_to_at_least_coder():
    # A bare "explain this code" is cheap, but a fenced code block bumps it.
    p = classify_task("解释这段代码 ```py\nx = 1\n```")
    assert p.recommended_level == ModelLevel.CODER


def test_two_file_globs_bump_to_agent():
    p = classify_task("更新 src/foo.py 和 src/bar.py 的实现")
    assert p.recommended_level == ModelLevel.AGENT
    assert p.complexity == "hard"


def test_caller_hint_wants_tools_bumps_to_agent():
    p = classify_task("什么是 Python 的 list？", wants_tools=True)
    assert p.recommended_level == ModelLevel.AGENT


def test_previous_failures_escalate_to_expert():
    p = classify_task("什么是 Python 的 list？", previous_failures=2)
    assert p.recommended_level == ModelLevel.EXPERT
