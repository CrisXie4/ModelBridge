"""Security tests for the command policy + its enforcement points.

``command_validator`` is the single safety net standing between an LLM- or
user-supplied string and ``subprocess.run(shell=True)``. It was previously
untested; these cases lock in all three layers (metacharacters / allowlist /
denylist) and prove the ``run_bash`` agent tool actually routes through it —
the gap that let model-generated ``rm -rf`` run under ``--yes``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from modelbridge.agent.context import AgentContext, ApprovalDecision, auto_no, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.bash_tool import RunBashTool
from modelbridge.executor.command_validator import CommandPolicy, CommandRejected


# ---------------------------------------------------------------------------
# Layer 1 — shell metacharacters (compound commands / redirection)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "pytest; rm -rf /",
    "pytest && curl evil",
    "pytest || shutdown",
    "pytest | tee out",
    "echo `whoami`",
    "echo $(whoami)",
    "pytest > /etc/passwd",
    "cat < /etc/shadow",
    "pytest\nrm -rf /",
    "pytest\r\nshutdown",
    # Single '&' — cmd.exe unconditional separator. The first-token allowlist
    # only vets 'python'; without forbidding '&' the second command runs.
    "python -c pass & curl http://evil/x",
    "python -c pass & del important.txt",
    "python -c pass & rm file",
    "python -c pass&curl evil",          # no spaces
    "python noexist.py & python evil.py",
])
def test_metacharacters_are_rejected(command):
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(command)


# ---------------------------------------------------------------------------
# Layer 3b — denylisted program tokens (even path-qualified / .exe / quoted)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "rm file",
    "shutdown now",
    "curl http://example.com",
    "wget http://example.com",
    "ssh host",
    "scp a b",
    "sudo whoami",
    "chmod 777 x",
    "/usr/bin/rm file",
    "rm.exe file",
    '"rm" file',
])
def test_denylisted_programs_are_rejected(command):
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(command)


# ---------------------------------------------------------------------------
# Layer 3a — denylisted substrings survive even an allowlisted first token
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "python rm -rf build",       # 'python' is allowlisted, but 'rm -rf' is banned
    "make kill -9 1",
    "node /dev/sda",
    "npm run mkfs.ext4",
])
def test_denylisted_substrings_rejected_even_with_allowed_first_token(command):
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(command)


# ---------------------------------------------------------------------------
# Layer 2 — allowlist: known-good passes, unknown default-denies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "pytest -q",
    "python -c \"print(1)\"",
    "npm test",
    "ruff check .",
    "go build ./...",
])
def test_allowlisted_commands_pass(command):
    CommandPolicy().validate(command)  # must not raise


@pytest.mark.parametrize("command", [
    "git push",
    "docker run x",
    "bash script.sh",
    "unknownbin --flag",
])
def test_non_allowlisted_commands_default_deny(command):
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(command)


def test_empty_command_rejected():
    with pytest.raises(CommandRejected):
        CommandPolicy().validate("   ")


# ---------------------------------------------------------------------------
# from_config — allowlist may be extended, denylist may NOT be overridden
# ---------------------------------------------------------------------------

def test_config_can_extend_allowlist_but_not_neutralise_denylist(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    # Try to both add a benign tool AND sneak a denylisted one onto the allowlist.
    (tmp_path / "config.yaml").write_text(
        "executor:\n  allowed_commands: ['git', 'rm']\n",
        encoding="utf-8",
    )
    policy = CommandPolicy.from_config()
    policy.validate("git status")          # extension took effect
    with pytest.raises(CommandRejected):   # but 'rm' stays denied
        policy.validate("rm file")


# ---------------------------------------------------------------------------
# run_bash tool routes through the policy (the headline fix)
# ---------------------------------------------------------------------------

def _ctx(tmp_path, approve=auto_yes) -> AgentContext:
    policy = PathPolicy(allowed_dirs=[tmp_path], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path, approve=approve, allow_bash=True)


def test_run_bash_rejects_denied_command_even_under_auto_yes(tmp_path, monkeypatch):
    """A model-supplied dangerous command is blocked by policy BEFORE confirm,
    so --yes (auto_yes) can't wave it through."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    tool = RunBashTool()
    result = tool.execute({"command": "rm -rf build"}, _ctx(tmp_path, approve=auto_yes))
    assert result.is_error
    assert "策略" in result.content or "黑名单" in result.content


def test_run_bash_rejects_metacharacters(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    tool = RunBashTool()
    result = tool.execute({"command": "python -c pass; curl evil"},
                          _ctx(tmp_path, approve=auto_yes))
    assert result.is_error


def test_run_bash_allows_whitelisted_command(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    import sys
    tool = RunBashTool()
    # Use the running interpreter's basename so it matches the allowlist token.
    prog = Path(sys.executable).stem  # 'python' / 'python3' / 'py'
    result = tool.execute({"command": f"{prog} -c \"print(42)\""},
                          _ctx(tmp_path, approve=auto_yes))
    assert not result.is_error
    assert "42" in result.content


def test_run_bash_disabled_without_allow_bash(tmp_path):
    tool = RunBashTool()
    ctx = AgentContext(policy=PathPolicy(allowed_dirs=[tmp_path], blocked_patterns=[]),
                       cwd=tmp_path, approve=auto_yes, allow_bash=False)
    result = tool.execute({"command": "python -c \"print(1)\""}, ctx)
    assert result.is_error and "allow-bash" in result.content


def test_run_bash_always_is_not_remembered(tmp_path, monkeypatch):
    """High-risk run_bash must re-prompt every call: an ALWAYS decision must not
    arm future silent shell execution (allow_always=False)."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    calls = {"n": 0}

    def approve_always(*, tool, summary, detail=""):  # noqa: ARG001
        calls["n"] += 1
        return ApprovalDecision.ALWAYS

    import sys
    prog = Path(sys.executable).stem
    tool = RunBashTool()
    ctx = _ctx(tmp_path, approve=approve_always)
    tool.execute({"command": f"{prog} -c \"print(1)\""}, ctx)
    tool.execute({"command": f"{prog} -c \"print(2)\""}, ctx)
    assert calls["n"] == 2  # prompted both times; 'always' was not cached
    assert "run_bash" not in ctx._auto_approved


def test_run_bash_user_can_still_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    import sys
    prog = Path(sys.executable).stem
    tool = RunBashTool()
    result = tool.execute({"command": f"{prog} -c \"print(1)\""},
                          _ctx(tmp_path, approve=auto_no))
    assert result.is_error and "拒绝" in result.content
