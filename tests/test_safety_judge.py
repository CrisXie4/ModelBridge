# tests/test_safety_judge.py
"""LLM safety-judge verdict parsing + persistent-approval short-circuit.

Regression this file pins: the old parser required the WHOLE judge reply to
contain "安全" and not "不安全" — but the reply's reasoning naturally restates
the unsafe categories ("不涉及支付等不安全操作"), so nearly every verdict
came back unsafe and auto-approval never passed.
"""

from __future__ import annotations

import pytest

from modelbridge.utils import _parse_safety_verdict, llm_safety_judge


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    (tmp_path / "models.yaml").write_text(
        "models:\n"
        "  - name: tiny1\n"
        "    provider: custom\n"
        "    base_url: https://gw.example.com/v1\n"
        "    model: some-model\n"
        "    level: tiny\n",
        encoding="utf-8",
    )
    return tmp_path


class _FakeProvider:
    def __init__(self, content: str):
        self.content = content

    def chat(self, req, *, timeout=15.0):  # noqa: ARG002
        from modelbridge.schemas import ChatResponse

        return ChatResponse(content=self.content)


# ---------------------------------------------------------------------------
# _parse_safety_verdict — the structured-conclusion parser
# ---------------------------------------------------------------------------

def test_reasoning_mentioning_unsafe_words_but_concluding_safe():
    """THE regression: reasoning restates unsafe categories, conclusion says
    安全 → must be judged safe (old substring logic said unsafe)."""
    reply = (
        "该操作只是关闭弹窗，不涉及支付/转账/删除账户等不安全行为，"
        "后果可撤销，属于低风险操作。\n结论：安全"
    )
    is_safe, matched = _parse_safety_verdict(reply)
    assert matched is True
    assert is_safe is True


def test_concluding_unsafe_is_unsafe():
    reply = "该操作会提交订单，涉及资金。\n结论：不安全"
    is_safe, matched = _parse_safety_verdict(reply)
    assert matched is True
    assert is_safe is False


def test_last_conclusion_line_wins():
    reply = "结论：安全\n再想想，这是支付操作。结论：不安全"
    is_safe, matched = _parse_safety_verdict(reply)
    assert matched is True
    assert is_safe is False


def test_fallback_last_line_bare_verdict():
    assert _parse_safety_verdict("低风险，可自动执行。安全") == (True, True)
    assert _parse_safety_verdict("涉及支付。不安全") == (False, True)


def test_no_verdict_fails_closed():
    assert _parse_safety_verdict("这个操作我觉得还行。") == (False, False)
    assert _parse_safety_verdict("") == (False, False)


# ---------------------------------------------------------------------------
# llm_safety_judge end-to-end (fake provider)
# ---------------------------------------------------------------------------

def test_judge_safe_verdict_passes(home, monkeypatch):
    import modelbridge.providers as providers_mod

    monkeypatch.setattr(
        providers_mod,
        "get_provider",
        lambda e: _FakeProvider("仅关闭弹窗，不涉及不安全类别，可撤销。\n结论：安全"),
    )
    is_safe, reason = llm_safety_judge(tool="click", summary="关闭弹窗", detail="...")
    assert is_safe is True
    assert "安全" in reason


def test_judge_unparseable_reply_fails_closed(home, monkeypatch):
    import modelbridge.providers as providers_mod

    monkeypatch.setattr(
        providers_mod,
        "get_provider",
        lambda e: _FakeProvider("嗯，这个说不好，看情况吧。"),
    )
    is_safe, reason = llm_safety_judge(tool="click", summary="?", detail="...")
    assert is_safe is False
    assert "格式异常" in reason


# ---------------------------------------------------------------------------
# Persistent approval short-circuit in _make_approval
# ---------------------------------------------------------------------------

def _forbid_prompt(monkeypatch):
    """If the manual prompt is reached, the test is wrong — fail loudly
    instead of hanging on stdin."""
    from rich.prompt import Prompt

    def _boom(*a, **k):  # pragma: no cover - only reached on regression
        raise AssertionError("manual approval prompt reached")

    monkeypatch.setattr(Prompt, "ask", _boom)


def test_permanent_always_pattern_auto_passes(home, monkeypatch):
    import json

    from modelbridge.agent.context import ApprovalDecision
    from modelbridge.cli import _make_approval

    (home / "approved_patterns.json").write_text(
        json.dumps({"browser_write": "click 关闭弹窗"}), encoding="utf-8"
    )
    _forbid_prompt(monkeypatch)
    ask = _make_approval(yes=False)
    decision = ask(tool="click", summary="关闭弹窗", detail="", save_pattern="browser_write")
    assert decision == ApprovalDecision.YES


def test_permanent_auto_pattern_enables_judge(home, monkeypatch):
    import json

    import modelbridge.utils as utils_mod
    from modelbridge.agent.context import ApprovalDecision
    from modelbridge.cli import _make_approval

    (home / "approved_patterns.json").write_text(
        json.dumps({"browser_write:auto": "click 关闭弹窗 [auto]"}), encoding="utf-8"
    )
    _forbid_prompt(monkeypatch)
    called = {}

    def _fake_judge(*, tool, summary, detail, reason=""):
        called["tool"] = tool
        return True, "低风险"

    monkeypatch.setattr(utils_mod, "llm_safety_judge", _fake_judge)
    ask = _make_approval(yes=False)
    decision = ask(
        tool="click", summary="关闭弹窗", detail="", save_pattern="browser_write", auto=False
    )
    assert called["tool"] == "click"  # judge ran despite auto=False
    assert decision == ApprovalDecision.YES


def test_unapproved_pattern_still_prompts(home, monkeypatch):
    """No saved pattern → behaviour unchanged: manual prompt (no judge run
    without auto=True). Simulate the user answering 'n'."""
    import json

    from rich.prompt import Prompt

    import modelbridge.utils as utils_mod
    from modelbridge.agent.context import ApprovalDecision
    from modelbridge.cli import _make_approval

    (home / "approved_patterns.json").write_text(
        json.dumps({"other_tool": "别的东西"}), encoding="utf-8"
    )
    monkeypatch.setattr(Prompt, "ask", lambda *a, **k: "n")
    monkeypatch.setattr(
        utils_mod, "llm_safety_judge", lambda **k: (_ for _ in ()).throw(
            AssertionError("judge should not run without auto=True")
        )
    )
    ask = _make_approval(yes=False)
    decision = ask(tool="click", summary="s", detail="", save_pattern="browser_write")
    assert decision == ApprovalDecision.NO  # user answered n
