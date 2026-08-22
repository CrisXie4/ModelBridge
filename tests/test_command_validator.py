"""Security tests for the command policy + its enforcement points.

``command_validator`` is the safety net between a user-supplied string and
the shell for the human-facing ``mbridge run``. These cases lock in the
layers: command-substitution ban / quote-aware segment split / allowlist /
denylist.

The agent-side ``run_bash`` tool deliberately does NOT route through the
policy any more — the per-command user confirmation is its gate. The
run_bash tests below pin that contract: compound commands execute verbatim
once approved, and one "always" covers the rest of the session.

NOTE: the scary-looking strings below are fixtures on purpose — this file
exists to prove they are rejected (mbridge run) or gated by confirmation
(run_bash). They are never passed to a real shell except the benign
``python -c print(...)`` smoke commands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from modelbridge.agent.context import AgentContext, ApprovalDecision, auto_no, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools.bash_tool import RunBashTool
from modelbridge.executor.command_validator import CommandPolicy, CommandRejected


# ---------------------------------------------------------------------------
# Layer 1a — command substitution is banned outright (it executes without
# any separator token, so no segment split can vet it)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "echo `whoami`",
    "echo $(whoami)",
    "pytest $(curl evil)",
])
def test_substitution_is_rejected(command):
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(command)


# ---------------------------------------------------------------------------
# Layer 1b — every ;|& segment must pass the allow/deny checks. Compound
# lines, pipes and redirects are fine as long as each segment's program is
# allowlisted.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "pytest; rm -rf /",               # banned substring + denied 2nd segment
    "pytest && curl evil",            # denied 2nd program
    "pytest || shutdown",
    "pytest | tee out",               # 'tee' not allowlisted
    "python -c pass & curl http://evil/x",
    "python -c pass & del important.txt",
    "python -c pass & rm file",
    "python -c pass&curl evil",       # no spaces — still split at '&'
    "pytest\nrm -rf /",               # newline is a separator
    "pytest\r\nshutdown",
    "cat < /etc/shadow",              # 'cat' not allowlisted
    "python rm -rf build && pytest",  # banned substring anywhere on the line
])
def test_compound_with_bad_segment_is_rejected(command):
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(command)


@pytest.mark.parametrize("command", [
    "go build ./... && go test ./...",
    "pytest -q > out.txt",            # redirects allowed
    "pytest > out.txt 2>&1",          # fd-merge must not split at its '&'
    "pytest -x | pytest -q",          # pipe with both sides allowlisted
    # The whitelist judges programs, not arguments — two allowlisted
    # programs joined with & run; that is the point of segment checking.
    "python noexist.py & python other.py",
])
def test_compound_with_all_segments_allowed_passes(command):
    CommandPolicy().validate(command)  # must not raise


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


def test_separators_only_command_rejected():
    with pytest.raises(CommandRejected):
        CommandPolicy().validate(";;")


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
    # Separator inside quotes is a literal, not a segment split — this is
    # the old blanket-metachar false positive, now fixed.
    policy.validate('git commit -m "fix; typo"')


# ---------------------------------------------------------------------------
# run_bash tool — no policy gate; the confirm prompt is the gate
# ---------------------------------------------------------------------------

def _ctx(tmp_path, approve=auto_yes) -> AgentContext:
    policy = PathPolicy(allowed_dirs=[tmp_path], blocked_patterns=[])
    return AgentContext(policy=policy, cwd=tmp_path, approve=approve, allow_bash=True)


def test_run_bash_runs_compound_commands(tmp_path, monkeypatch):
    """Pipes / ; / && execute verbatim once approved — no allowlist, no
    metacharacter rejection on the AI path."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    tool = RunBashTool()
    prog = Path(sys.executable).stem
    result = tool.execute(
        {"command": f'{prog} -c "print(7)" && {prog} -c "print(8)"'},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not result.is_error
    assert "7" in result.content and "8" in result.content


def test_run_bash_runs_quoted_metacharacters(tmp_path, monkeypatch):
    """The old layer rejected `;` even inside quoted literals — the very
    false positive that made the tool unusable for real work."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    tool = RunBashTool()
    prog = Path(sys.executable).stem
    result = tool.execute(
        {"command": f"{prog} -c \"print('a;b')\""},
        _ctx(tmp_path, approve=auto_yes),
    )
    assert not result.is_error
    assert "a;b" in result.content


def test_run_bash_allows_whitelisted_command(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    tool = RunBashTool()
    # Use the running interpreter's basename so it runs on any platform.
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


def test_run_bash_always_is_remembered_for_session(tmp_path, monkeypatch):
    """One ALWAYS decision covers the rest of the session (allow_always=True).
    The old re-prompt-every-call behaviour made 'always' a lie for run_bash."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    calls = {"n": 0}

    def approve_always(*, tool, summary, detail="", reason="",  # noqa: ARG001
                       save_pattern=None, auto=False):  # noqa: ARG001
        calls["n"] += 1
        return ApprovalDecision.ALWAYS

    prog = Path(sys.executable).stem
    tool = RunBashTool()
    ctx = _ctx(tmp_path, approve=approve_always)
    tool.execute({"command": f"{prog} -c \"print(1)\""}, ctx)
    tool.execute({"command": f"{prog} -c \"print(2)\""}, ctx)
    assert calls["n"] == 1  # second call was auto-approved
    assert "run_bash" in ctx._auto_approved


def test_run_bash_user_can_still_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    prog = Path(sys.executable).stem
    tool = RunBashTool()
    result = tool.execute({"command": f"{prog} -c \"print(1)\""},
                          _ctx(tmp_path, approve=auto_no))
    assert result.is_error and "拒绝" in result.content
