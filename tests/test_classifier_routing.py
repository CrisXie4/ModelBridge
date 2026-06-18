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
