# tests/test_bash_tool_encoding.py
"""run_bash delegates to the hardened runner and passes its decoded output
through unchanged. (Decode-fallback itself is covered by
test_runner_encoding.py; here we pin the tool's contract.)"""

from __future__ import annotations

from modelbridge.agent.context import AgentContext, auto_yes
from modelbridge.agent.security import PathPolicy
from modelbridge.agent.tools import bash_tool as bash_mod
from modelbridge.agent.tools.background_tool import BackgroundTaskManager
from modelbridge.agent.tools.bash_tool import RunBashTool, _auto_background_eligible
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


# --- 超时移交后台（U9）：非 sleep 命令超时不杀进程，转后台继续跑 -------------


def test_auto_background_eligible_policy():
    """对齐 ZCode bash-background-policy：首 token 为 sleep 不转后台。"""
    assert _auto_background_eligible("npm run build")
    assert _auto_background_eligible("  git push origin main")
    assert not _auto_background_eligible("sleep 5")
    assert not _auto_background_eligible("   ")
    assert not _auto_background_eligible("")


def test_timeout_hands_off_to_background(tmp_path):
    """超时 → 进程不杀、转后台继续跑完，输出进任务、通知回调触发。"""
    notices: list[str] = []
    mgr = BackgroundTaskManager(on_complete=lambda text, task: notices.append(text))
    tool = RunBashTool(manager=mgr)
    # 跨平台的长命令：python 自身 sleep 2s 后打印标记（sleep 不是首 token）。
    cmd = 'python -c "import time; time.sleep(2); print(\'bg-finished-marker\')"'
    res = tool.execute({"command": cmd, "timeout": 1}, _ctx(tmp_path))

    assert not res.is_error
    assert "转入后台" in res.content
    assert res.structured["backgrounded"] is True
    task_id = res.structured["id"]
    assert task_id.startswith("bg-")

    # 后台进程继续跑完：任务最终 done，且捕获到移交之后才打印的输出。
    final = mgr.wait(task_id, timeout=30)
    assert final is not None and final["status"] == "done"
    assert final["exit_code"] == 0
    assert res.structured["output_chars"] >= 0
    assert notices, "完成通知必须经过 on_complete 回调"
    assert "bg-finished-marker" in mgr.get(task_id).output


def test_sleep_command_stays_on_legacy_kill_path(tmp_path, monkeypatch):
    """sleep 类命令不转后台：超时仍走旧行为（杀进程 + 报错），零授权面变化。"""
    adopted: list = []
    mgr = BackgroundTaskManager()
    monkeypatch.setattr(mgr, "adopt_process", lambda *a, **k: adopted.append(a))
    _patch(monkeypatch, _result(timed_out=True, exit_code=-1))
    res = RunBashTool(manager=mgr).execute(
        {"command": "sleep 999", "timeout": 1}, _ctx(tmp_path)
    )
    assert res.is_error
    assert "超时" in res.content
    assert adopted == []  # 未移交


def test_unmanaged_timeout_keeps_legacy_behavior(tmp_path, monkeypatch):
    """未注入 manager（如旧调用方）时行为与升级前完全一致。"""
    _patch(monkeypatch, _result(timed_out=True, exit_code=-1))
    res = RunBashTool().execute({"command": "make all", "timeout": 1}, _ctx(tmp_path))
    assert res.is_error
    assert "超时" in res.content


def test_handoff_path_normal_completion_matches_legacy(tmp_path):
    """有 manager 时，未超时的命令结果与旧路径同构（exit/输出/truncated）。"""
    mgr = BackgroundTaskManager()
    tool = RunBashTool(manager=mgr)
    res = tool.execute({"command": "echo hello-from-handoff"}, _ctx(tmp_path))
    assert not res.is_error
    assert res.structured["exit"] == 0
    assert "hello-from-handoff" in res.content
    assert res.structured.get("backgrounded") is not True  # 正常结束无移交标记
