# tests/test_bash_tool_encoding.py
"""run_bash delegates to the hardened runner and passes its decoded output
through unchanged. (Decode-fallback itself is covered by
test_runner_encoding.py; here we pin the tool's contract.)"""

from __future__ import annotations

from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools import bash_tool as bash_mod
from modelbridge.agent.tools.bash_tool import RunBashTool
from modelbridge.executor.runner import CommandResult


def _ctx(tmp_path):
    policy = PathPolicy(allowed_dirs=[tmp_path.resolve()], blocked_patterns=[])
    return AgentContext(
        policy=policy, cwd=tmp_path.resolve(), approve=auto_yes, allow_bash=True
    )


def _result(stdout="ok", stderr="", exit_code=0, truncated=False, timed_out=False):
    return CommandResult(
        command="x",
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_ms=1,
        truncated=truncated,
        timed_out=timed_out,
    )


def _patch(monkeypatch, result):
    monkeypatch.setattr(bash_mod, "run_command", lambda *a, **k: result)
    # Decouple from the command allowlist — these tests are about output handling.
    monkeypatch.setattr(
        bash_mod.CommandPolicy,
        "from_config",
        lambda: type("P", (), {"validate": lambda self, cmd: None})(),
    )


def test_bash_tool_passes_decoded_output_through(tmp_path, monkeypatch):
    # Non-ASCII (CJK) output must arrive intact, not mangled.
    _patch(monkeypatch, _result(stdout="构建成功 好的"))
    res = RunBashTool().execute({"command": "echo hi"}, _ctx(tmp_path))

    assert not res.is_error
    assert "构建成功 好的" in res.content
    assert res.structured["exit"] == 0


def test_bash_tool_reports_timeout(tmp_path, monkeypatch):
    _patch(monkeypatch, _result(timed_out=True, exit_code=-1))
    res = RunBashTool().execute({"command": "sleep 999"}, _ctx(tmp_path))

    assert res.is_error
    assert "超时" in res.content


def test_bash_tool_truncation_message_uses_chars_not_bytes(tmp_path, monkeypatch):
    long_stdout = "好" * 9000  # > _MAX_OUTPUT (8000) characters
    _patch(monkeypatch, _result(stdout=long_stdout, truncated=True))
    res = RunBashTool().execute({"command": "echo hi"}, _ctx(tmp_path))

    assert res.structured["truncated"] is True
    assert "字符" in res.content
    assert "字节" not in res.content
