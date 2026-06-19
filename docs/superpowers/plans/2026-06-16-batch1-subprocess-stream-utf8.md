# Batch 1 — Subprocess & Stream UTF-8 Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop CJK output corruption and unhandled errors at every place ModelBridge spawns a subprocess or reconfigures a stdio stream, so a 国产模型优先 + Windows (GBK) user gets readable, robust output.

**Architecture:** Three subprocess/stream sites currently rely on the OS-default encoding (GBK on Chinese Windows). We make each one explicitly UTF-8 with `errors="replace"`, matching the proven pattern already in `mcp/transport/stdio.py`. We also fix one user-facing truncation message that mislabels characters as bytes, and harden the MCP server's stdio reconfigure to swallow `ValueError` (not just `AttributeError`) by extracting it into a testable helper.

**Tech Stack:** Python 3.11, `subprocess`, `pytest`, `monkeypatch`.

**Environment note:** `mbridge` runs on **Python 3.11** (per project memory — the default `python` may be a venv without `typer`). Run the test commands below with the Python 3.11 interpreter that has the project's dev deps. All Batch-1 tests deliberately avoid importing `modelbridge.cli`, so they do not require `typer`.

**Scope boundary:** This batch covers the 4 theme-1 items with clean, locale-independent unit tests. Deferred to later batches (with their natural siblings): rules_loader byte-budget truncation (→ prompt-correctness batch, needs `RuleFile`), REPL `UnicodeDecodeError` warning + `cli_console` reconfigure logging (→ CLI-papercut batch), rich-Live CJK width (L-effort, already mitigated by the v4 tail-view).

---

### Task 1: bash_tool — decode subprocess output as UTF-8

**Files:**
- Modify: `modelbridge/agent/tools/bash_tool.py:96-105`
- Test: `tests/test_bash_tool_encoding.py` (create)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bash_tool_encoding.py::test_bash_tool_runs_subprocess_as_utf8 -v`
Expected: FAIL — `captured.get("encoding")` is `None` (current code passes no `encoding`).

- [ ] **Step 3: Add explicit UTF-8 decoding**

In `modelbridge/agent/tools/bash_tool.py`, change the `subprocess.run(...)` call (lines 96-105) to:

```python
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(ctx.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                # On Windows, subprocess uses cmd.exe with shell=True; on POSIX /bin/sh.
                check=False,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bash_tool_encoding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_bash_tool_encoding.py modelbridge/agent/tools/bash_tool.py
git commit -m "fix(bash_tool): decode subprocess output as UTF-8 to avoid GBK corruption on Windows

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: executor/runner — decode Popen output as UTF-8

**Files:**
- Modify: `modelbridge/executor/runner.py:78-84`
- Test: `tests/test_runner_encoding.py` (create)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner_encoding.py::test_run_command_decodes_as_utf8 -v`
Expected: FAIL — `captured.get("encoding")` is `None`.

- [ ] **Step 3: Add encoding to popen_kwargs**

In `modelbridge/executor/runner.py`, change the `popen_kwargs` dict (lines 78-84) to:

```python
    popen_kwargs: dict = dict(
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner_encoding.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_runner_encoding.py modelbridge/executor/runner.py
git commit -m "fix(executor): decode subprocess output as UTF-8 in run_command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: bash_tool — truncation message says 字符, not 字节

**Files:**
- Modify: `modelbridge/agent/tools/bash_tool.py:122`
- Test: `tests/test_bash_tool_encoding.py` (add to the file from Task 1)

The truncation check at line 115 uses `len(combined)` (a **character** count) but the message at line 122 says `字节` (bytes). With CJK text 1 char = 3 bytes, so the message is wrong. The minimal correct fix is to match the message to the unit actually used: characters.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_bash_tool_encoding.py

def test_bash_tool_truncation_message_uses_chars_not_bytes(tmp_path, monkeypatch):
    long_stdout = "好" * 9000  # > _MAX_OUTPUT (8000) characters

    def fake_run(command, **kwargs):
        return _FakeCompleted(stdout=long_stdout)

    _patch(monkeypatch, fake_run)
    res = RunBashTool().execute({"command": "echo hi"}, _ctx(tmp_path))

    assert res.structured["truncated"] is True
    assert "字符" in res.content
    assert "字节" not in res.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bash_tool_encoding.py::test_bash_tool_truncation_message_uses_chars_not_bytes -v`
Expected: FAIL — message currently contains `字节`, not `字符`.

- [ ] **Step 3: Fix the message**

In `modelbridge/agent/tools/bash_tool.py`, change line 122 from:

```python
            body += f"\n\n[... 输出超过 {_MAX_OUTPUT} 字节已截断 ...]"
```

to:

```python
            body += f"\n\n[... 输出超过 {_MAX_OUTPUT} 字符已截断 ...]"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bash_tool_encoding.py -v`
Expected: PASS (both Task 1 and Task 3 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_bash_tool_encoding.py modelbridge/agent/tools/bash_tool.py
git commit -m "fix(bash_tool): truncation message says 字符 (chars), matching the char-based limit

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: mcp/server — stdio reconfigure swallows ValueError too

**Files:**
- Modify: `modelbridge/mcp/server/server.py:118-125`
- Test: `tests/test_mcp_server_reconfigure.py` (create)

`serve_stdio()` reconfigures stdin/stdout to UTF-8 but only catches `AttributeError`. When a stream is redirected/closed, `reconfigure()` can raise `ValueError` ("I/O operation on closed file"), crashing the server. Extract the reconfigure into a module-level helper `_reconfigure_stdio()` (testable) that catches both.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server_reconfigure.py
"""_reconfigure_stdio must not crash when reconfigure() raises ValueError."""

from __future__ import annotations

from modelbridge.mcp.server import server as server_mod


class _BadStream:
    def reconfigure(self, **kwargs):
        raise ValueError("I/O operation on closed file")


def test_reconfigure_stdio_swallows_valueerror(monkeypatch):
    monkeypatch.setattr(server_mod.sys, "stdin", _BadStream())
    monkeypatch.setattr(server_mod.sys, "stdout", _BadStream())
    # Must return without raising.
    server_mod._reconfigure_stdio()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server_reconfigure.py -v`
Expected: FAIL — `AttributeError: module 'modelbridge.mcp.server.server' has no attribute '_reconfigure_stdio'`.

- [ ] **Step 3: Add the helper and call it from serve_stdio**

In `modelbridge/mcp/server/server.py`, add this module-level function (place it just above the class that defines `serve_stdio`, next to the existing `_log` helper):

```python
def _reconfigure_stdio() -> None:
    """Switch stdin/stdout to UTF-8 (Windows pipes default to GBK).

    Best-effort: swallows AttributeError (non-standard streams in tests /
    embedding) and ValueError (e.g. 'I/O operation on closed file' when a
    stream is redirected or already closed).
    """
    for _stream in (sys.stdin, sys.stdout):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
```

Then replace the inline reconfigure block in `serve_stdio()` (lines 119-125) with a single call:

```python
    def serve_stdio(self) -> int:
        # MCP frames are UTF-8; on Windows the default pipe encoding is GBK,
        # which mangles CJK payloads in both directions.
        _reconfigure_stdio()
        _log(f"{self.name} v{self.version} serving MCP on stdio "
             f"({len(self.tools)} tools)")
```

(Leave the rest of `serve_stdio` — the `for line in sys.stdin:` loop — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server_reconfigure.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_mcp_server_reconfigure.py modelbridge/mcp/server/server.py
git commit -m "fix(mcp/server): stdio reconfigure swallows ValueError, not just AttributeError

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Batch verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass (the 3 new test files plus the existing suite — no regressions).

- [ ] **Step 2: Manual CJK smoke check (Windows)**

On a Chinese Windows box (GBK locale), run:

Run: `mbridge run "python -c \"print('模型: 国产模型优先')\""`
Expected: output shows `模型: 国产模型优先` correctly (not garbled `════`).

If not on Windows, this step is N/A — the unit tests already assert the `encoding="utf-8"` kwarg is passed, which is the locale-independent guarantee.

- [ ] **Step 3: Confirm no stray `text=True`-without-encoding remains in this batch's files**

Run: `git grep -n "text=True" modelbridge/agent/tools/bash_tool.py modelbridge/executor/runner.py`
Expected: each match line is immediately followed (within the same call) by `encoding="utf-8"` and `errors="replace"`.

---

## Self-Review

- **Spec coverage (theme 1):** bash encoding ✓ (Task 1), runner encoding ✓ (Task 2), bash truncation 字节→字符 ✓ (Task 3), mcp/server reconfigure ValueError ✓ (Task 4). Deferred items explicitly listed in Scope boundary with their target batch — no silent drops.
- **Placeholder scan:** none — every code/test step shows full content and exact commands.
- **Type/name consistency:** `_FakeCompleted`, `_patch`, `_ctx` defined in Task 1 and reused in Task 3 (same file). `_reconfigure_stdio` defined and called consistently in Task 4. `run_command` / `RunBashTool` signatures match the real source read during planning.
