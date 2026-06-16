# tests/test_bash_tool_encoding.py
"""run_bash must decode subprocess output as UTF-8 (Windows pipes default to GBK)."""

from __future__ import annotations

from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools import bash_tool as bash_mod
from modelbridge.agent.tools.bash_tool import RunBashTool


class _FakeCompleted:
    def __init__(self, stdout="ok", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _ctx(tmp_path):
    policy = PathPolicy(allowed_dirs=[tmp_path.resolve()], blocked_patterns=[])
    return AgentContext(
        policy=policy, cwd=tmp_path.resolve(), approve=auto_yes, allow_bash=True
    )


def _patch(monkeypatch, fake_run):
    monkeypatch.setattr(bash_mod.subprocess, "run", fake_run)
    # Decouple from the command allowlist — this test is only about encoding.
    monkeypatch.setattr(
        bash_mod.CommandPolicy,
        "from_config",
        lambda: type("P", (), {"validate": lambda self, cmd: None})(),
    )


def test_bash_tool_runs_subprocess_as_utf8(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return _FakeCompleted()

    _patch(monkeypatch, fake_run)
    res = RunBashTool().execute({"command": "echo hi"}, _ctx(tmp_path))

    assert not res.is_error
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"


def test_bash_tool_truncation_message_uses_chars_not_bytes(tmp_path, monkeypatch):
    long_stdout = "好" * 9000  # > _MAX_OUTPUT (8000) characters

    def fake_run(command, **kwargs):
        return _FakeCompleted(stdout=long_stdout)

    _patch(monkeypatch, fake_run)
    res = RunBashTool().execute({"command": "echo hi"}, _ctx(tmp_path))

    assert res.structured["truncated"] is True
    assert "字符" in res.content
    assert "字节" not in res.content
