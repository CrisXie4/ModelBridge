"""Tool-output bounding with spill-to-disk — preview, pointer, retention.

Covers :mod:`modelbridge.agent.tools.output_spill`:

* In-budget output passes through untouched (``truncated=False``).
* Over-budget output spills the full text to ``~/.modelbridge/tool-output``
  and returns a line-snapped head+tail preview whose marker carries the
  spill path (plus a delegate hint when the session is ``delegable``).
* Spill failures degrade to a plain truncation (never raise).
* The lazy sweep deletes only files older than ``RETENTION_DAYS`` and is
  rate-limited unless forced.
"""

from __future__ import annotations

import time
from pathlib import Path

from modelbridge.agent.tools import output_spill
from modelbridge.agent.tools.output_spill import (
    RETENTION_DAYS,
    bound_output,
    spill_output,
    sweep_spills,
)


def test_within_budget_passthrough() -> None:
    body, truncated = bound_output("hello\nworld", tool="run_bash", max_chars=100)
    assert (body, truncated) == ("hello\nworld", False)


def test_over_budget_spills_and_previews(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(output_spill, "spill_root", lambda: tmp_path / "tool-output")
    lines = "".join(f"line-{i:05d} {'x' * 60}\n" for i in range(200))
    body, truncated = bound_output(lines, tool="run_bash", max_chars=2_000)

    assert truncated is True
    assert len(body) < 3_000  # preview stays near the budget
    # Marker carries the spill path + read_file hint.
    assert "read_file" in body
    spill_path = Path(body.split("完整输出已保存到 ", 1)[1].split("，", 1)[0])
    assert spill_path.exists()
    # Full content survived on disk.
    assert spill_path.read_text(encoding="utf-8") == lines
    # Preview is line-snapped: starts on a complete first line, keeps the
    # final line intact, and ends clean.
    assert body.startswith("line-00000")
    assert "line-00199" in body
    assert body.endswith("\n")


def test_delegable_adds_subagent_hint(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(output_spill, "spill_root", lambda: tmp_path / "tool-output")
    body, _ = bound_output("x" * 9_999, tool="run_bash", max_chars=1_000, delegable=True)
    assert "spawn_subagent" in body


def test_spill_failure_degrades_to_plain_truncation(monkeypatch, tmp_path: Path) -> None:
    def broken_spill(tool: str, content: str, *, now=None):
        return None

    monkeypatch.setattr(output_spill, "spill_output", broken_spill)
    body, truncated = bound_output("y" * 5_000, tool="run_bash", max_chars=1_000)
    assert truncated is True
    assert "read_file" not in body
    assert "已截断" in body


def test_spill_output_exclusive_names(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "tool-output"
    monkeypatch.setattr(output_spill, "spill_root", lambda: root)
    p1 = spill_output("run_bash", "a", now=1_000_000)
    p2 = spill_output("run_bash", "b", now=1_000_000)
    assert p1 is not None and p2 is not None and p1 != p2
    assert p1.read_text(encoding="utf-8") == "a"


def test_sweep_removes_only_old_files(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "tool-output"
    root.mkdir(parents=True)
    old = root / "run_bash_old.txt"
    new = root / "run_bash_new.txt"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    now = time.time()
    old_stamp = now - (RETENTION_DAYS + 1) * 86400
    import os

    os.utime(old, (old_stamp, old_stamp))

    monkeypatch.setattr(output_spill, "spill_root", lambda: root)
    removed = sweep_spills(now=now, force=True)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_sweep_rate_limited_unless_forced(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "tool-output"
    root.mkdir(parents=True)
    monkeypatch.setattr(output_spill, "spill_root", lambda: root)
    assert sweep_spills(now=100.0, force=True) == 0  # nothing to remove
    stale = root / "run_bash_stale.txt"
    stale.write_text("x", encoding="utf-8")
    import os

    stamp = 100.0 - (RETENTION_DAYS + 2) * 86400
    os.utime(stale, (stamp, stamp))
    # Non-forced call within the sweep interval is a no-op even though a
    # stale file exists.
    assert sweep_spills(now=100.0 + 30) == 0
    assert stale.exists()
    # Forced call removes it.
    assert sweep_spills(now=100.0 + 30, force=True) == 1
    assert not stale.exists()
