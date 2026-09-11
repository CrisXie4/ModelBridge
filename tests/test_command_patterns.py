"""Bash "always" approval patterns — prefix widening, segment matching.

Covers :mod:`modelbridge.agent.command_patterns`:

* Splitting a compound command into per-simple-command segments (quoted
  metacharacters stay intact; pipes/&&/; all break segments).
* Arity-driven prefix patterns: ``git push`` widens to ``git push *``, not
  ``git *`` — one level down, never the whole tool.
* Matching requires EVERY segment of the candidate command to match some
  recorded pattern.
* AgentContext integration: ``bash_allowed`` / ``approve_bash`` and the
  (decision, feedback) tuple normalization on ``confirm``.
"""

from __future__ import annotations

from modelbridge.agent.command_patterns import (
    command_matches,
    command_patterns,
    split_segments,
)
from modelbridge.agent.context import AgentContext, ApprovalDecision, auto_no
from modelbridge.agent.security import PathPolicy


# ---------------------------------------------------------------------------
# split_segments
# ---------------------------------------------------------------------------

def test_split_simple() -> None:
    assert split_segments("git status") == [["git", "status"]]


def test_split_compound_operators() -> None:
    assert split_segments("a && b || c; d | e") == [["a"], ["b"], ["c"], ["d"], ["e"]]


def test_split_keeps_quoted_metacharacters() -> None:
    segs = split_segments('git commit -m "fix; broken && stuff"')
    assert segs == [["git", "commit", "-m", "fix; broken && stuff"]]


def test_split_unmatched_quotes_falls_back() -> None:
    # shlex raises on the unbalanced quote; whitespace fallback must still
    # produce usable segments (stricter, never looser).
    segs = split_segments('echo "unterminated && cat')
    assert segs and segs[0][0] == "echo"


def test_split_redirection_and_subshell_punct() -> None:
    segs = split_segments("sort < in.txt > out.txt")
    assert segs == [["sort", "in.txt", "out.txt"]]


# ---------------------------------------------------------------------------
# command_patterns
# ---------------------------------------------------------------------------

def test_pattern_single_command_uses_arity() -> None:
    assert command_patterns("git push origin main") == ["git push *"]


def test_pattern_bare_prefix_command_still_records() -> None:
    assert command_patterns("git push") == ["git push *"]


def test_pattern_unknown_command_defaults_arity_one() -> None:
    assert command_patterns("frobnicate --all") == ["frobnicate *"]


def test_pattern_compound_records_one_per_segment() -> None:
    pats = command_patterns("git add . && git commit -m x | cat")
    assert pats == ["git add *", "git commit *", "cat *"]


def test_pattern_dedupes() -> None:
    assert command_patterns("git add a && git add b") == ["git add *"]


def test_pattern_quoted_semicolon_not_split() -> None:
    assert command_patterns('git commit -m "a; b"') == ["git commit *"]


# ---------------------------------------------------------------------------
# command_matches
# ---------------------------------------------------------------------------

def test_match_exact_and_extension() -> None:
    pats = ["git push *"]
    assert command_matches("git push", pats)
    assert command_matches("git push origin main --force", pats)


def test_match_rejects_sibling_subcommands() -> None:
    assert not command_matches("git config user.name x", ["git push *"])
    assert not command_matches("git reset --hard", ["git push *"])


def test_match_requires_every_segment() -> None:
    pats = ["git add *", "git commit *"]
    assert command_matches("git add . && git commit -m y", pats)
    assert not command_matches("git add . && rm -rf /", pats)


def test_match_no_patterns_is_false() -> None:
    assert not command_matches("git status", [])


def test_match_empty_command_is_false() -> None:
    assert not command_matches("   ", ["git *"])


# ---------------------------------------------------------------------------
# AgentContext integration
# ---------------------------------------------------------------------------

def _ctx() -> AgentContext:
    return AgentContext(policy=PathPolicy(allowed_dirs=[]), cwd=None, approve=auto_no)


def test_context_bash_allowed_roundtrip() -> None:
    ctx = _ctx()
    assert not ctx.bash_allowed("git push origin")
    ctx.approve_bash("git push origin main")
    assert ctx.bash_allowed("git push origin main")
    assert ctx.bash_allowed("git push")
    assert not ctx.bash_allowed("git config user.name x")


def test_context_confirm_stores_tuple_feedback() -> None:
    ctx = _ctx()

    def approve(**_: object):
        return (ApprovalDecision.NO, "不要动这个文件，改用方案 B")

    ctx.approve = approve
    ok = ctx.confirm(tool="run_bash", summary="s", detail="d")
    assert ok is False
    assert ctx.last_decision == ApprovalDecision.NO
    assert ctx.last_feedback == "不要动这个文件，改用方案 B"


def test_context_confirm_plain_decision_clears_feedback() -> None:
    ctx = _ctx()
    decisions = iter([(ApprovalDecision.NO, "先别") , ApprovalDecision.NO])

    def approve(**_: object):
        return next(decisions)

    ctx.approve = approve
    ctx.confirm(tool="t", summary="s")
    assert ctx.last_feedback == "先别"
    ctx.confirm(tool="t", summary="s")
    assert ctx.last_decision == ApprovalDecision.NO
    assert ctx.last_feedback is None


def test_context_confirm_whitespace_feedback_is_none() -> None:
    ctx = _ctx()

    def approve(**_: object):
        return (ApprovalDecision.NO, "   ")

    ctx.approve = approve
    ctx.confirm(tool="t", summary="s")
    assert ctx.last_feedback is None
