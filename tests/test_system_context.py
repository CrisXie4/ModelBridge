"""System context baseline + incremental updates (Context Epoch, right-sized).

Covers :mod:`modelbridge.context.system_context`:

* First observation emits one ``<system-update>`` baseline message and
  records the snapshot in ``session.metadata``.
* Unchanged values emit nothing (the prefix cache keeps hitting).
* Changed values emit an update line; a source that becomes unobservable
  keeps its previously admitted value (no spurious removal); a source
  that loads empty emits its removal line.
* Snapshot advances atomically with the returned message.
* A compaction commit invalidates the snapshot, so the next prepare
  re-baselines (facts survive being summarized away).
* ``maybe_apply_context_updates`` respects the
  ``prompt.system_context_updates`` config flag and never raises.
"""

from __future__ import annotations

from pathlib import Path

from modelbridge.agent.session import Session
from modelbridge.context.system_context import (
    SNAPSHOT_KEY,
    ContextSource,
    SystemContextTracker,
    date_source,
    default_sources,
    git_branch_source,
    maybe_apply_context_updates,
)
from modelbridge.schemas import ChatMessage


def _session() -> Session:
    s = Session(model_name="test-model")
    s.add_system("SYSTEM PREFIX")
    s.add_user("hello")
    return s


def _src(key: str, values: list[str | None]) -> ContextSource:
    """A scripted source popping successive values; None = unavailable.
    Once the script is exhausted the load returns None (unavailable)."""
    queue = list(values)

    def load() -> str | None:
        if not queue:
            return None
        return queue.pop(0)

    return ContextSource(
        key=key,
        load=load,
        baseline=lambda v: f"[{key}] base={v}",
        update=lambda prev, cur: f"[{key}] {prev} -> {cur}",
        removed=lambda prev: f"[{key}] gone (was {prev})",
    )


# ---------------------------------------------------------------------------
# Tracker behaviour
# ---------------------------------------------------------------------------

def test_first_prepare_emits_baseline_and_records_snapshot() -> None:
    s = _session()
    tracker = SystemContextTracker([_src("a", ["v1"])])
    msg = tracker.prepare(s)
    assert isinstance(msg, ChatMessage) and msg.role == "user"
    assert "<system-update>" in msg.content
    assert "[a] base=v1" in msg.content
    assert s.metadata[SNAPSHOT_KEY] == {"a": "v1"}


def test_unchanged_value_emits_nothing() -> None:
    s = _session()
    tracker = SystemContextTracker([_src("a", ["v1", "v1", "v1"])])
    assert tracker.prepare(s) is not None
    assert tracker.prepare(s) is None
    assert tracker.prepare(s) is None
    assert len(s.messages) == 2  # system + user, nothing appended


def test_changed_value_emits_update_line() -> None:
    s = _session()
    tracker = SystemContextTracker([_src("a", ["v1", "v2"])])
    first = tracker.prepare(s)
    assert first is not None
    s.messages.append(first)
    second = tracker.prepare(s)
    assert second is not None
    assert "[a] v1 -> v2" in second.content
    assert s.metadata[SNAPSHOT_KEY] == {"a": "v2"}


def test_unobservable_emits_removal_when_previously_admitted() -> None:
    s = _session()
    tracker = SystemContextTracker([_src("a", ["v1", None, None])])
    s.messages.append(tracker.prepare(s))
    msg = tracker.prepare(s)  # not observable now → gone
    assert msg is not None
    assert "[a] gone (was v1)" in msg.content
    assert s.metadata[SNAPSHOT_KEY] == {}
    # Next turn: still unobservable, nothing previously admitted → silent.
    assert tracker.prepare(s) is None


def test_empty_load_renders_removal() -> None:
    s = _session()
    tracker = SystemContextTracker([_src("a", ["v1", ""])])
    s.messages.append(tracker.prepare(s))
    msg = tracker.prepare(s)
    assert msg is not None
    assert "[a] gone (was v1)" in msg.content
    assert s.metadata[SNAPSHOT_KEY] == {}


def test_load_exception_treated_as_unavailable() -> None:
    def boom() -> str | None:
        raise RuntimeError("io down")

    src = ContextSource(
        key="b",
        load=boom,
        baseline=lambda v: f"[b] base={v}",
        update=lambda p, c: f"[b] {p} -> {c}",
        removed=lambda p: "[b] gone",
    )
    s = _session()
    tracker = SystemContextTracker([src])
    assert tracker.prepare(s) is None
    assert s.metadata[SNAPSHOT_KEY] == {}


# ---------------------------------------------------------------------------
# Built-in sources
# ---------------------------------------------------------------------------

def test_date_source_renders_iso_date_and_weekday() -> None:
    src = date_source()
    value = src.load()
    assert value is not None
    assert len(value.split(" ")) == 2
    assert src.baseline(value).startswith("今天是 ")


def test_git_branch_source_reads_head(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    src = git_branch_source(tmp_path)
    assert src.load() == "main"
    # Detached HEAD → short hash.
    (git_dir / "HEAD").write_text("abc1234567890def\n", encoding="utf-8")
    assert src.load() == "abc123456789"


def test_git_branch_source_worktree(tmp_path: Path) -> None:
    main_git = tmp_path / "main" / ".git"
    worktree_git = main_git.parent / "main-git" / "worktrees" / "wt1"
    worktree_git.mkdir(parents=True)
    (worktree_git / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")
    assert git_branch_source(wt).load() == "feature"


def test_git_branch_source_no_repo(tmp_path: Path) -> None:
    assert git_branch_source(tmp_path).load() is None


def test_default_sources_order() -> None:
    keys = [s.key for s in default_sources(Path("."))]
    assert keys == ["core/date", "core/git-branch"]


# ---------------------------------------------------------------------------
# Compaction invalidation
# ---------------------------------------------------------------------------

def test_compaction_invalidates_snapshot_and_rebaselines() -> None:
    from modelbridge.agent.compact import compact_session

    big = "关于项目结构的说明。" * 40
    s = Session(model_name="test-model")
    s.add_system("SYSTEM")
    s.add_user("u1 " + big)
    s.add_assistant(type("R", (), {"content": "a " + big, "tool_calls": None,
                                   "reasoning_content": None, "raw_message": None})())
    s.add_user("u2 " + big)
    s.add_assistant(type("R", (), {"content": "b " + big, "tool_calls": None,
                                   "reasoning_content": None, "raw_message": None})())

    # Seed a snapshot as if a baseline had been admitted earlier.
    s.metadata[SNAPSHOT_KEY] = {"core/date": "2020-01-01 周三"}

    summary_box = []

    def summarize(text: str) -> str:
        summary_box.append(text)
        return "检查点摘要"

    tracker = SystemContextTracker([date_source()])
    result = compact_session(s, keep_turns=1, model_name="test-model",
                             summarize_fn=summarize)
    assert result.ok and result.removed_count > 0
    # Compaction popped the snapshot…
    assert SNAPSHOT_KEY not in s.metadata
    # …so the next prepare re-baselines instead of claiming nothing changed.
    msg = tracker.prepare(s)
    assert msg is not None and "今天是" in msg.content


# ---------------------------------------------------------------------------
# Config gate + never-raises contract
# ---------------------------------------------------------------------------

def test_apply_respects_config_flag(monkeypatch, tmp_path: Path) -> None:
    from modelbridge.config import AppConfig

    cfg = AppConfig()
    cfg.prompt.system_context_updates = False
    import modelbridge.config as config_mod

    monkeypatch.setattr(config_mod, "load_app_config", lambda: cfg)
    s = _session()
    maybe_apply_context_updates(s, cwd=tmp_path)
    assert len(s.messages) == 2  # nothing appended when disabled


def test_apply_emits_baseline_with_real_sources(monkeypatch, tmp_path: Path) -> None:
    from modelbridge.config import AppConfig

    cfg = AppConfig()
    import modelbridge.config as config_mod

    monkeypatch.setattr(config_mod, "load_app_config", lambda: cfg)
    s = _session()
    maybe_apply_context_updates(s, cwd=tmp_path)
    assert len(s.messages) == 3
    update_msg = s.messages[-1]
    assert update_msg.role == "user"
    assert "<system-update>" in update_msg.content
