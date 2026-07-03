"""Browser write tools share one "always" approval; REPL folds long output."""

from __future__ import annotations

from pathlib import Path

from modelbridge.agent.context import AgentContext, ApprovalDecision
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.browser_write_tools import ClickTool, FillTool, NavigateTool


class _FakeBridge:
    def call(self, name, args, *, timeout=None):  # noqa: ARG002
        return {"ok": True, "content": f"did {name}"}


def _ctx(approve):
    return AgentContext(
        policy=PathPolicy(allowed_dirs=[], blocked_patterns=[]),
        cwd=Path.cwd(),
        approve=approve,
        browser_bridge=_FakeBridge(),
    )


def test_always_on_one_write_tool_approves_all_browser_writes():
    asks = {"n": 0}

    def approve(*, tool, summary, detail="",  # noqa: ARG001
                save_pattern=None, auto=False):  # noqa: ARG001
        asks["n"] += 1
        return ApprovalDecision.ALWAYS

    ctx = _ctx(approve)
    r1 = FillTool().execute({"selector": "#kw", "value": "金价"}, ctx)
    r2 = ClickTool().execute({"selector": "#su"}, ctx)
    r3 = NavigateTool().execute({"url": "https://example.com"}, ctx)

    assert not r1.is_error and not r2.is_error and not r3.is_error
    # ALWAYS on fill auto-approved click + navigate too (shared group).
    assert asks["n"] == 1


def test_no_approval_still_blocks_each():
    asks = {"n": 0}

    def approve(*, tool, summary, detail="",  # noqa: ARG001
                save_pattern=None, auto=False):  # noqa: ARG001
        asks["n"] += 1
        return ApprovalDecision.NO

    ctx = _ctx(approve)
    r1 = FillTool().execute({"selector": "#kw", "value": "x"}, ctx)
    r2 = ClickTool().execute({"selector": "#su"}, ctx)
    assert r1.is_error and r2.is_error
    assert asks["n"] == 2  # each denial re-asks


def test_fold_tool_body():
    from modelbridge.cli import _fold_tool_body

    long = "\n".join(f"line{i}" for i in range(30))
    folded = _fold_tool_body(long)
    assert "折叠" in folded and "模型已获取完整内容" in folded
    assert folded.count("\n") <= 8  # head lines + note, not all 30

    assert _fold_tool_body("hello") == "hello"  # short content untouched
    assert _fold_tool_body("") == ""
