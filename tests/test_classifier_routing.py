"""Routing-classifier correctness regressions."""

from __future__ import annotations

from modelbridge.models import ModelLevel
from modelbridge.router.classifier import classify_task


def test_same_file_twice_does_not_escalate_to_agent():
    # One file mentioned twice is a single-file edit → CODER, not AGENT.
    p = classify_task("please change src/utils.py then change src/utils.py once more")
    assert p.recommended_level == ModelLevel.CODER


def test_two_distinct_files_escalate_to_agent():
    p = classify_task("please change src/a.py and also tests/b.py")
    assert p.recommended_level == ModelLevel.AGENT


def test_ascii_keyword_does_not_substring_false_match():
    # 'class' must NOT match inside 'classifier'.
    p = classify_task("the classifier is a bit slow today")
    assert p.recommended_level == ModelLevel.CHEAP
    assert "class" not in p.matched_keywords


def test_standalone_ascii_keyword_still_matches():
    p = classify_task("write a class for the User model")
    assert p.task_type == "code_generate"


def test_cjk_keyword_still_substring_matches():
    # CJK keywords have no word boundary; substring matching must still work.
    p = classify_task("帮我写代码实现一个登录函数")
    assert p.task_type == "code_generate"
