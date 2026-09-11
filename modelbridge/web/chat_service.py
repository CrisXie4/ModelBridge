"""Embedded AI chat for the local admin backend.

One service owns every conversation created through ``/api/chats``. Each
turn runs :func:`run_agent_turn` (the same engine as the REPL and the
browser bridge) on a worker thread and appends protocol-ish events into a
per-chat ring the frontend long-polls::

    {"seq": N, "type": "delta" | "tool" | "assistant" | "approval" | ...}

Approvals block the worker thread on an event until the dashboard answers
via ``POST /api/approvals/<id>`` — same semantics as the bridge's approval
cards. Turns are serialized webui-wide by one lock (the registry and cwd
are shared, exactly like ``bridge.host.Host`` serializes its turns).

Conversations live in memory for the lifetime of ``mbridge web``; disk
persistence is intentionally deferred (restarts are rare for a local
admin process, and replaying old chats was not requested).
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Module-level so tests can monkeypatch name resolution in one place.
from ..client import resolve_model_name

_IDS = itertools.count(1)


@dataclass
class _PendingApproval:
    aid: str
    chat_id: str
    tool: str = ""
    summary: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    decision: str = "n"
    # Free text the user typed when rejecting — rides back to the model in
    # the tool's error message (see AgentContext.last_feedback).
    feedback: str = ""


class ChatConversation:
    """One chat: history + event log + current-turn bookkeeping."""

    def __init__(self, cid: int, title: str) -> None:
        self.id = f"c{cid}"
        self.title = title or f"会话 {cid}"
        self.session: Any | None = None
        self.model_name: str | None = None
        self.events: list[dict[str, Any]] = []
        self.seq = 0
        self.busy = False
        self.cancel_requested = False
        self.pending: _PendingApproval | None = None

    def emit(self, etype: str, **fields: Any) -> dict[str, Any]:
        with _LOCK:
            self.seq += 1
            ev = {"seq": self.seq, "type": etype, **fields}
            # Ring cap: full replay stays bounded; the client sees enough
            # history for an admin session, no eviction UI required.
            if len(self.events) > 4000:
                self.events = self.events[-3000:]
            self.events.append(ev)
            return ev


_LOCK = threading.Lock()


class ChatService:
    """Owns conversations + the single webui agent turn."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd or Path.cwd()
        self.chats: dict[str, ChatConversation] = {}
        self.counter = _IDS
        self.turn_lock = threading.Lock()
        self.pending_map: dict[str, _PendingApproval] = {}

    # -- lifecycle -----------------------------------------------------------

    def list_chats(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [
                {"id": c.id, "title": c.title, "busy": c.busy,
                 "events": len(c.events)}
                for c in sorted(self.chats.values(), key=lambda c: c.id)
            ]

    def create(self, title: str = "") -> dict[str, Any]:
        chat = ChatConversation(next(self.counter), title.strip())
        with _LOCK:
            self.chats[chat.id] = chat
        return {"id": chat.id, "title": chat.title}

    def delete(self, chat_id: str) -> bool:
        chat = self.chats.get(chat_id)
        if chat is None:
            return False
        if chat.pending is not None:
            self.resolve_approval(chat.pending.aid, "n")
        with _LOCK:
            self.chats.pop(chat_id, None)
        return True

    def get(self, chat_id: str) -> ChatConversation | None:
        return self.chats.get(chat_id)

    def snapshot(self, chat_id: str, after: int) -> dict[str, Any] | None:
        chat = self.get(chat_id)
        if chat is None:
            return None
        with _LOCK:
            events = [e for e in chat.events if e["seq"] > after]
        return {
            "id": chat.id,
            "title": chat.title,
            "busy": chat.busy,
            "pending": (
                {"id": chat.pending.aid, "tool": chat.pending.tool,
                 "summary": chat.pending.summary}
                if chat.pending else None
            ),
            "events": events,
        }

    # -- approvals -----------------------------------------------------------

    def resolve_approval(self, approval_id: str, decision: str,
                         feedback: str = "") -> bool:
        pend = self.pending_map.pop(approval_id, None)
        if pend is None:
            return False
        pend.decision = decision
        pend.feedback = (feedback or "").strip()
        pend.done.set()
        chat = self.chats.get(pend.chat_id)
        if chat is not None:
            chat.emit("approval_done", approval_id=approval_id, decision=decision)
        return True

    # -- turn execution --------------------------------------------------------

    def submit(self, chat_id: str, text: str, model: str | None,
               default_model: str | None, timeout: float = 180.0) -> dict[str, Any]:
        chat = self.get(chat_id)
        if chat is None:
            return {"ok": False, "error": "会话不存在"}
        if not text.strip():
            return {"ok": False, "error": "空消息"}
        if chat.busy or self.turn_lock.locked():
            return {"ok": False, "error": "busy：已有任务在执行"}

        chat.busy = True
        chat.cancel_requested = False
        chat.emit("user", text=text)

        worker = threading.Thread(
            target=self._run_turn,
            args=(chat, text, model or default_model),
            kwargs={"timeout": timeout},
            daemon=True,
            name=f"web-chat-{chat.id}",
        )
        worker.start()
        return {"ok": True}

    # The next three helpers are closures over module-level imports used by
    # _run_turn; kept as small methods so tests can monkeypatch providers at
    # the loop level exactly like tests/test_bridge_host.py does.

    def _build_session(self, chat: ChatConversation, model_name: str) -> Any:
        from ..agent.session import Session

        if chat.session is None:
            chat.session = Session(model_name=model_name)
        return chat.session

    def _emit_events_for(self, chat: ChatConversation):
        def on_content_delta(t: str) -> None:
            chat.emit("delta", kind="content", text=t)

        def on_reasoning_delta(t: str) -> None:
            chat.emit("delta", kind="reasoning", text=t)

        def on_tool_call(call: Any, out: str, is_err: bool = False) -> None:
            preview = ", ".join(f"{k}=…" for k in dict(call.arguments))[:80]
            chat.emit("tool", name=call.name, args=preview, error=bool(is_err))

        def on_system(text: str) -> None:
            chat.emit("notice", text=text)

        return on_content_delta, on_reasoning_delta, on_tool_call, on_system

    def _run_turn(self, chat: ChatConversation, text: str, model_name: str,
                  timeout: float) -> None:
        from ..agent.context import AgentContext, ApprovalDecision
        from ..agent.loop import run_agent_turn
        from ..agent.security import PathPolicy
        from ..agent.tools.registry import build_default_registry

        try:
            model_name = resolve_model_name(model_name)
        except Exception as e:
            chat.busy = False
            chat.emit("error", message=str(e))
            chat.emit("done")
            return

        session = self._build_session(chat, model_name)
        session.add_user(text)
        chat.model_name = model_name

        registry = build_default_registry(include_bash=True)

        registry_lock = self.turn_lock
        acquired = registry_lock.acquire(timeout=5)
        if not acquired:
            chat.busy = False
            chat.emit("error", message="busy：另一轮任务尚未结束")
            chat.emit("done")
            return

        def approve(*, tool: str, summary: str, detail: str = "",
                    reason: str = "", **_: Any
                    ) -> "ApprovalDecision | tuple[ApprovalDecision, str]":
            aid = f"a{next(self.counter)}"
            pend = _PendingApproval(aid=aid, chat_id=chat.id, tool=tool, summary=summary)
            self.pending_map[aid] = pend
            chat.pending = pend
            chat.emit("approval", approval_id=aid, tool=tool,
                      summary=summary, detail=detail, reason=reason)
            # No timeout on purpose — the user decides. Deleting the chat or
            # resolving via POST /api/approvals sets the event.
            pend.done.wait()
            chat.pending = None
            chat.emit("decision", approval_id=aid, decision=pend.decision)
            if pend.decision != "y":
                # Rejection carries optional user feedback as a (decision,
                # feedback) tuple — AgentContext.confirm stores it and
                # run_bash embeds it in the model-facing error.
                if pend.feedback:
                    return (ApprovalDecision.NO, pend.feedback)
                return ApprovalDecision.NO
            return ApprovalDecision.YES

        # 与 REPL 相同的路径策略：mbridge web 的 cwd 即聊天的工作目录。
        try:
            from ..agent.security import PathPolicy as _PP

            policy = _PP.from_config(extra_cwd=self.cwd)
        except Exception:  # pragma: no cover — config-less fallback
            policy = PathPolicy(allowed_dirs=[self.cwd], blocked_patterns=[])

        on_content, on_reasoning, on_tool, on_system = self._emit_events_for(chat)
        try:
            result = run_agent_turn(
                session=session,
                ctx=AgentContext(
                    policy=policy,
                    cwd=self.cwd,
                    approve=approve,
                    allow_bash=True,
                    # No subagent tool in the web chat registry → truncation
                    # hints stay with read_file, no delegate suggestion.
                    delegable=False,
                ),
                registry=registry,
                model_name=model_name,
                timeout=timeout,
                stream=True,
                on_content_delta=on_content,
                on_reasoning_delta=on_reasoning,
                on_tool_call=on_tool,
                on_system=on_system,
            )
            final = result.final_response
            content = (final.content if final else "") or ""
            if result.stopped_reason == "provider_error" and result.error is not None:
                chat.emit("error", message=str(result.error.message))
            else:
                chat.emit("assistant", content=content)
        except Exception as e:  # noqa: BLE001 — surface to the dashboard
            import traceback

            detail = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc()
            if tb and "\n" in tb:
                detail += "\n" + "\n".join(tb.strip().splitlines()[-6:])
            chat.emit("error", message=detail)
        finally:
            registry_lock.release()
            chat.emit("done")
            chat.busy = False


_SERVICE: ChatService | None = None


def get_chat_service() -> ChatService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ChatService()
    return _SERVICE


__all__ = ["ChatService", "ChatConversation", "get_chat_service"]
