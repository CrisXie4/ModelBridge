# tests/test_runner_encoding.py
"""run_command captures child output as bytes and decodes it tolerantly.

Windows console tools emit in the active code page (cp936/GBK on Chinese
Windows); a hard-coded UTF-8 decode would mangle that into U+FFFD. The
runner now prefers UTF-8 and falls back to the OS locale / GBK.
"""

from __future__ import annotations

from modelbridge.executor import runner as runner_mod
from modelbridge.executor.runner import run_command


class _FakeProc:
    returncode = 0
    pid = 4321

    def __init__(self, out=b"", err=b""):
        self._out = out
        self._err = err

    def communicate(self, timeout=None):
        return (self._out, self._err)


def _patch_popen(monkeypatch, proc):
    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return proc

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
    return captured


def test_run_command_captures_bytes_not_text(tmp_path, monkeypatch):
    captured = _patch_popen(monkeypatch, _FakeProc(out=b"ok"))
    res = run_command("python --version", cwd=tmp_path)

    assert res.exit_code == 0
    assert res.stdout == "ok"
    # Bytes mode: no text/encoding hand-off to Popen (we decode ourselves).
    assert captured.get("encoding") is None
    assert not captured.get("text")


def test_run_command_decodes_utf8(tmp_path, monkeypatch):
    _patch_popen(monkeypatch, _FakeProc(out="构建成功".encode("utf-8")))
    res = run_command("x", cwd=tmp_path)
    assert res.stdout == "构建成功"


def test_run_command_falls_back_to_gbk(tmp_path, monkeypatch):
    # cp936/GBK bytes are invalid UTF-8 → must fall through to the GBK decode
    # rather than being replaced with U+FFFD. Portable across UTF-8 hosts
    # because the runner always includes an explicit GBK attempt.
    _patch_popen(monkeypatch, _FakeProc(out="构建成功".encode("gbk")))
    res = run_command("x", cwd=tmp_path)
    assert res.stdout == "构建成功"
    assert "�" not in res.stdout
