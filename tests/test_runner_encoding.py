# tests/test_runner_encoding.py
"""run_command must decode Popen output as UTF-8 (Windows pipes default to GBK)."""

from __future__ import annotations

from modelbridge.executor import runner as runner_mod
from modelbridge.executor.runner import run_command


class _FakeProc:
    returncode = 0
    pid = 4321

    def communicate(self, timeout=None):
        return ("out", "")


def test_run_command_decodes_as_utf8(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
    res = run_command("python --version", cwd=tmp_path)

    assert res.exit_code == 0
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"
