"""System context baseline + incremental updates — opencode's Context Epoch,
right-sized for ModelBridge's byte-stable-prefix doctrine.

The harness cache rule says the leading prompt prefix must stay byte-stable
across a session, so dynamic facts (today's date, current git branch) can't
be baked into the system prompt: they would either invalidate the provider's
KV cache on every change or go stale silently. opencode solves this with a
**Context Epoch** — a frozen baseline plus mid-conversation system updates
admitted only at safe provider-turn boundaries (``specs/v2/session.md`` in
the opencode tree).

ModelBridge keeps its prefix strictly static; this module provides the
missing *update channel*:

* Environment facts are observed at every step boundary (after tool
  results settle, before the next request is built — a safe boundary:
  nothing mid-tool-group is ever disturbed).
* The first observation is recorded as the baseline snapshot and emitted
  as one ``<system-update>`` message; later turns emit a message only when
  a fact **changes** (or disappears). Between changes nothing is appended,
  so the prefix cache keeps hitting.
* Updates ride as ``role=user`` messages wrapped in ``<system-update>``
  tags — the same choice opencode makes for providers without a
  mid-conversation system role, and it matches how the compaction
  checkpoint is already delivered (all OpenAI-compatible domestic
  providers accept it).
* A compaction commit invalidates the snapshot (``compact_session`` pops
  ``session.metadata["system_context_snapshot"]``): the checkpoint may
  have summarized the baseline message away, so the next turn re-baselines
  and the facts survive compaction.

Gone-is-gone: a source that stops observing (no git repo, unreadable HEAD,
load raising) emits its ``removed`` line if a value was previously admitted
— silence only when there was never an admitted value. The next successful
observation re-baselines. This collapses opencode's "unavailable vs
removed" distinction on purpose: ModelBridge's sources are cheap local
reads where a vanished fact is worth announcing and a transient miss
self-heals on the next turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..schemas import ChatMessage
from ..agent.session import Session

__all__ = [
    "SNAPSHOT_KEY",
    "ContextSource",
    "SystemContextTracker",
    "date_source",
    "git_branch_source",
    "default_sources",
    "maybe_apply_context_updates",
]

#: ``session.metadata`` key holding the admitted snapshot
#: (``{source_key: last_admitted_value}``).
SNAPSHOT_KEY = "system_context_snapshot"

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass(frozen=True)
class ContextSource:
    """One independently observed fact in the session's system context.

    ``load`` returns the current value, or ``None``/``""`` when the fact is
    not observable right now (treated as "gone": the removal line renders if
    a value was previously admitted). The three renderers turn values into
    the chronological message lines: first admission (``baseline``), later
    change (``update``), and disappearance (``removed``).
    """

    key: str
    load: Callable[[], str | None]
    baseline: Callable[[str], str]
    update: Callable[[str, str], str]
    removed: Callable[[str], str] | None = None


def date_source() -> ContextSource:
    """Today's date — the classic midnight-rollover fact."""
    from datetime import date as _date

    def _load() -> str:
        today = _date.today()
        return f"{today.isoformat()} {_WEEKDAYS[today.weekday()]}"

    return ContextSource(
        key="core/date",
        load=_load,
        baseline=lambda v: f"今天是 {v}。",
        update=lambda prev, cur: f"日期已变更：{prev} → {cur}。"
        "跨天了——注意「今天/昨天/明天」等相对时间表述随新日期计算。",
        removed=None,  # the date is never unobservable
    )


def _read_git_branch(cwd: Path) -> str | None:
    """Current git branch via ``.git/HEAD`` — one small file read, no subprocess.

    Handles worktrees (``.git`` as a file with ``gitdir: <path>``) and
    detached HEAD (returns the short commit hash).
    """
    git_path = cwd / ".git"
    try:
        if git_path.is_file():
            text = git_path.read_text(encoding="utf-8", errors="replace").strip()
            if ":" not in text:
                return None
            target = text.split(":", 1)[1].strip()
            git_dir = Path(target)
            if not git_dir.is_absolute():
                git_dir = cwd / target
            head = git_dir / "HEAD"
        elif git_path.is_dir():
            head = git_path / "HEAD"
        else:
            return None
        content = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if content.startswith("ref: refs/heads/"):
        return content[len("ref: refs/heads/") :]
    return content[:12] if content else None


def git_branch_source(cwd: Path) -> ContextSource:
    """Current git branch of the session cwd."""
    return ContextSource(
        key="core/git-branch",
        load=lambda: _read_git_branch(cwd),
        baseline=lambda v: f"当前 git 分支：{v}。",
        update=lambda prev, cur: f"git 分支已切换：{prev} → {cur}。"
        "工作区文件状态可能与此前的观察不同，重要结论请重新验证。",
        removed=lambda prev: f"当前目录已不在 git 仓库中（此前在分支 {prev}）。",
    )


def default_sources(cwd: Path) -> list[ContextSource]:
    """The built-in source set — mirror of opencode's ``core/environment`` +
    ``core/date``, bounded to what ModelBridge actually uses."""
    return [date_source(), git_branch_source(cwd)]


class SystemContextTracker:
    """Diffs observed source values against the admitted snapshot.

    State lives in ``session.metadata[SNAPSHOT_KEY]``, so the tracker itself
    is stateless and cheap to construct per call. :meth:`prepare` advances
    the snapshot and returns the message to append — the caller must append
    it at the safe boundary immediately (the loop does).
    """

    def __init__(self, sources: list[ContextSource]) -> None:
        self.sources = list(sources)

    def prepare(self, session: Session) -> ChatMessage | None:
        snapshot: dict[str, str] = dict(session.metadata.get(SNAPSHOT_KEY) or {})
        fresh: dict[str, str] = {}
        lines: list[str] = []
        for src in self.sources:
            try:
                value = src.load()
            except Exception:
                value = None
            if not value:
                # Unavailable: keep the previously admitted value; a source
                # that is *definitively* gone renders its removal line.
                if src.key in snapshot and src.removed is not None:
                    lines.append(src.removed(snapshot[src.key]))
                    snapshot.pop(src.key, None)
                continue
            fresh[src.key] = value
            if src.key not in snapshot:
                lines.append(src.baseline(value))
            elif snapshot[src.key] != value:
                lines.append(src.update(snapshot[src.key], value))
        # Advance the snapshot atomically with the returned message — the
        # caller appends it at the same boundary, so history and snapshot
        # can never disagree about what the model has been told.
        session.metadata[SNAPSHOT_KEY] = fresh
        if not lines:
            return None
        body = "\n".join(lines)
        return ChatMessage(
            role="user",
            content=f"<system-update>\n{body}\n</system-update>",
        )


def maybe_apply_context_updates(
    session: Session,
    *,
    cwd: Path,
) -> None:
    """Step-boundary entry point used by :func:`agent.loop.run_agent_turn`.

    Reads the ``prompt.system_context_updates`` config flag (default on),
    observes the default sources, and appends at most one
    ``<system-update>`` message. Never raises — a failure to observe or
    emit must never break the turn.
    """
    try:
        from ..config import load_app_config

        cfg = load_app_config().prompt
        if not getattr(cfg, "system_context_updates", True):
            return
    except Exception:
        return
    try:
        message = SystemContextTracker(default_sources(cwd)).prepare(session)
    except Exception:
        return
    if message is not None:
        session.messages.append(message)
