"""后台任务运行期输出 tail（E8）：status/面板运行中即可见最近输出。

worker 泵线程增量维护有界 tail（≤2000 字符）进 ``_BTask.output_tail``，
to_dict 纯附加字段；U9 超时移交（adopt_process）的长构建因此不必干等完成
通知。覆盖：增量可见（早期输出先于晚期出现）、最终 tail 等于输出的末窗、
to_dict 字段、run_command 路径完成后的统一契约。
"""

from __future__ import annotations

import subprocess
import time

from modelbridge.agent.tools.background_tool import (
    _RUNNING_TAIL_CHARS,
    BackgroundTaskManager,
)

_EARLY = "EARLY" * 300   # 1500 chars — 先输出
_LATE = "LATE" * 300     # 1500 chars — sleep 后再输出

_CHILD = (
    "import sys, time; "
    "sys.stdout.write('EARLY' * 300); sys.stdout.flush(); "
    "time.sleep(1.5); "
    "sys.stdout.write('LATE' * 300); sys.stdout.flush()"
)


def test_adopted_process_tail_is_incremental_and_bounded(tmp_path):
    """移交后运行期即可见早期输出；最终 tail = 输出末窗（≤2000 字符）。"""
    mgr = BackgroundTaskManager()
    proc = subprocess.Popen(
        ["python", "-c", _CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    info = mgr.adopt_process("long-build", proc)
    task_id = info["id"]
    assert info["output_tail"] == ""  # 刚接管时还没读到输出

    # 轮询等早期输出出现（此时进程应仍在跑晚期段）。
    deadline = time.monotonic() + 15
    saw_early_while_running = False
    while time.monotonic() < deadline:
        st = mgr.status(task_id)
        assert st is not None
        if "EARLY" in st["output_tail"]:
            if st["status"] == "running" and "LATE" not in st["output_tail"]:
                saw_early_while_running = True
            break
        time.sleep(0.05)
    assert saw_early_while_running, "运行期必须能增量看到早期输出"

    final = mgr.wait(task_id, timeout=30)
    assert final is not None and final["status"] == "done"
    task = mgr.get(task_id)
    assert task is not None
    assert "LATE" in task.output and "EARLY" in task.output  # 全量输出完整
    assert len(task.output_tail) <= _RUNNING_TAIL_CHARS
    assert task.output_tail == task.output[-_RUNNING_TAIL_CHARS:]
    assert final["output_tail"] == task.output_tail  # to_dict 纯附加字段


def test_start_task_to_dict_carries_output_tail_after_done(tmp_path):
    """run_command 路径：完成后 tail 与输出末窗一致（统一契约）。"""
    mgr = BackgroundTaskManager()
    cmd = "echo bg-tail-marker"
    info = mgr.start(cmd, cwd=str(tmp_path), timeout=30)
    final = mgr.wait(info["id"], timeout=30)
    assert final is not None and final["status"] == "done"
    assert "bg-tail-marker" in final["output_tail"]


def test_running_tail_window_bounded_on_rapid_output(tmp_path):
    """快速大输出（>2000 字符）→ tail 只保留末窗，不无限增长。"""
    mgr = BackgroundTaskManager()
    proc = subprocess.Popen(
        ["python", "-c", "import sys; sys.stdout.write('X' * 6000); sys.stdout.flush()"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    info = mgr.adopt_process("rapid", proc)
    final = mgr.wait(info["id"], timeout=30)
    assert final is not None and final["status"] == "done"
    assert len(final["output_tail"]) == _RUNNING_TAIL_CHARS
    assert set(final["output_tail"]) == {"X"}
