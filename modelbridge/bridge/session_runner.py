"""Drive one agent turn for the side panel and emit protocol frames.

Wraps :func:`modelbridge.agent.loop.run_agent_turn` (the same engine the REPL
uses) and translates its streaming callbacks into Native Messaging frames:

* ``on_content_delta``  -> ``delta{kind:"content"}``
* ``on_reasoning_delta``-> ``delta{kind:"reasoning"}``
* end of turn           -> ``assistant`` + ``done``
* provider failure      -> ``error`` + ``done{stopped:"provider_error"}``

Browser tools reach the page through a per-turn :class:`BrowserBridge`, which
is registered as the host's ``pending_router`` for the duration of the turn so
inbound ``tool_result`` / ``approval_result`` frames find their waiter. The
:class:`Session` persists across turns so the panel accumulates context.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..agent.context import AgentContext, ApprovalDecision
from ..agent.loop import run_agent_turn
from ..agent.security import PathPolicy
from ..agent.session import Session
from ..agent.tools.browser_tools import build_browser_registry
from ..client import ChatError, resolve_model_name
from ..providers import ProviderError
from ..utils import get_logger
from . import protocol as P
from .browser_bridge import BrowserBridge

if TYPE_CHECKING:
    from .host import Host

SendFn = Callable[[dict[str, Any]], None]

_SYSTEM_PROMPT = (
    "你是 ModelBridge 的浏览器侧边栏助手。用简洁的中文回答。"
    "你能读取和操作用户当前正在看的网页：\n"
    "- read_page / get_selection / query_dom / extract: 读正文、选区、定位元素、提取内容\n"
    "- page_scroll: 滚动页面 (长页面分段读取)\n"
    "- click / fill / press_key / navigate: 点击、填写、按键 (Enter 提交等)、跳转 (需确认)\n"
    "- inject_js: 在页面里执行 JS 并取回结果 (需确认)\n"
    "- get_cookies: 查看当前站点 Cookie (敏感，每次都需用户确认)\n"
    "- net_start / net_log / net_stop: 记录并查看页面网络请求——想看接口数据时"
    "先 net_start，再触发操作 (click / press_key / navigate)，然后 net_log 查看\n"
    "需要页面信息时主动调用工具，不要凭空猜测页面内容。回答要直接、可执行。\n"
    "页面正在加载时，工具会自动等到加载完成后再执行；navigate / click 触发跳转后"
    "也会等新页面加载完再返回。所以遇到加载慢的页面，继续调用工具完成任务即可，"
    "不要中途放弃、不要让用户自己去刷新或查看页面。"
)


def _llm_safety_judge(
    *, tool: str, summary: str, detail: str, reason: str = "",
) -> tuple[bool, str]:
    """Ask a tiny/cheap model whether a browser action is safe to auto-approve.

    Thin wrapper over the shared :func:`modelbridge.utils.llm_safety_judge`
    (same implementation as the CLI REPL and weixin gateway). On any failure
    (no model available, provider error, timeout) it returns ``(False, ...)``
    so the caller falls through to the manual side-panel approval — never
    blocks the action.
    """
    from modelbridge.utils import llm_safety_judge

    return llm_safety_judge(tool=tool, summary=summary, detail=detail, reason=reason)


class SessionRunner:
    """Owns the conversation history and runs turns against the engine."""

    def __init__(self, host: "Host") -> None:
        self._host = host
        self._send = host.send
        self._log = get_logger()
        self.session: Session | None = None
        self.model_name: str | None = None
        # Session-scoped "approve always" memory. A fresh AgentContext is built
        # each turn, so this set must live here (not on the per-turn ctx) for
        # ApprovalDecision.ALWAYS to persist across messages.
        self._approved_tools: set[str] = set()
        # Session-scoped auto mode (approval card's 自动 button): every
        # confirmation is AI-safety-judged instead of carded.
        self._auto_mode: bool = False
        # The panel's own todo list (the todo tool writes here; the state
        # frame publishes it to the rail).
        from ..agent.live_state import TodoStore

        self._todos = TodoStore()
        self._cache_hits = 0
        self._cache_misses = 0

    # ------------------------------------------------------------------
    # Status snapshot → panel rail (cwd / cache / todos)
    # ------------------------------------------------------------------

    def _fmt_cache(self, hit: int, total: int) -> str:
        if total <= 0:
            return ""
        return f"{hit * 100 // total}% ({hit})"

    def _send_state(self, turn_id: str = "") -> None:
        s_total = self._cache_hits + self._cache_misses
        self._send(P.state(
            id=turn_id,
            cwd=str(Path.cwd()),
            cache_last=self._fmt_cache(self._last_hit, self._last_hit + self._last_miss),
            cache_session=self._fmt_cache(self._cache_hits, s_total),
            todos=self._todos.to_list(),
        ))

    _last_hit: int = 0
    _last_miss: int = 0

    def _record_usage(self, resp: Any) -> None:
        try:
            from ..cache.manager import extract_cache_tokens

            hit, miss = extract_cache_tokens(getattr(resp, "usage", None))
        except Exception:
            return
        if hit or miss:
            self._last_hit, self._last_miss = hit, miss
            self._cache_hits += hit
            self._cache_misses += miss

    def _ensure_session(self, model_name: str) -> Session:
        if self.session is None:
            self.session = Session(model_name=model_name)
            self.session.add_system(_SYSTEM_PROMPT)
        return self.session

    def _build_context(self, bridge: BrowserBridge) -> AgentContext:
        def approve(*, tool: str, summary: str, detail: str = "", reason: str = "",
                    save_pattern: str | None = None, auto: bool = False) -> ApprovalDecision:
            # ``save_pattern`` is a CLI-side concern (persistent approval to
            # disk). The bridge round-trip doesn't support it; silently drop.
            _ = save_pattern
            # ── Context-aware auto-approval (LLM safety judge) ─────────────
            # When ``auto`` is set (browser write tools pass auto=True, or the
            # user enabled auto mode via the approval card's 自动 button), ask
            # a tiny model whether this action is safe given the model's
            # stated ``reason`` + target. Safe → auto-approve without bugging
            # the user. Unsafe → fall through to the side-panel card.
            # Failures (no model / network) also fall through to the panel —
            # never block on the judge.
            if auto or self._auto_mode:
                is_safe, judge_reason = _llm_safety_judge(
                    tool=tool, summary=summary, detail=detail, reason=reason,
                )
                if is_safe:
                    self._log.info("bridge.auto_approve tool=%s reason=%s",
                                   tool, judge_reason[:120])
                    return ApprovalDecision.YES
                if judge_reason:
                    self._log.info("bridge.auto_judge_unsafe tool=%s reason=%s",
                                   tool, judge_reason[:120])
            decision = bridge.request_approval(
                tool=tool, summary=summary, detail=detail, reason=reason,
            )
            if decision == "auto":
                # 自动 button: this session's confirmations go through the
                # AI safety judge from now on; the current one is approved.
                self._auto_mode = True
                return ApprovalDecision.YES
            return {
                "yes": ApprovalDecision.YES,
                "always": ApprovalDecision.ALWAYS,
                "no": ApprovalDecision.NO,
            }.get(decision, ApprovalDecision.NO)

        # No filesystem tools are registered for the panel, so the path policy
        # is unused — but the dataclass requires one.
        policy = PathPolicy(allowed_dirs=[], blocked_patterns=[])
        return AgentContext(
            policy=policy,
            cwd=Path.cwd(),
            approve=approve,
            browser_bridge=bridge,
            # Share the session-scoped set so "approve always" sticks across turns.
            _auto_approved=self._approved_tools,
            _auto_mode=self._auto_mode,
        )

    def build_registry(self):
        """Browser tools + the todo tool (rail 任务 section)."""
        registry = build_browser_registry(include_write=True)
        from ..agent.tools import TodoTool

        registry.register(TodoTool(self._todos))
        # Todo changes republish the rail state immediately.
        self._todos._on_change = lambda: self._send_state()
        return registry

    def _log_tool_call(self, call: Any, out: str, is_err: bool = False) -> None:
        """``run_agent_turn`` 的 on_tool_call 钩子 — 桥接轮次的工具流水进日志。

        带默认值的 lambda 会让 mypy 报 "Cannot infer type of lambda"，所以落成小方法。
        """
        self._log.info(
            "bridge.turn tool=%s%s", call.name, " (error)" if is_err else ""
        )

    def run(
        self,
        msg: dict[str, Any],
        *,
        reply_send: SendFn | None = None,
        tool_send: SendFn | None = None,
        approval_send: SendFn | None = None,
    ) -> None:
        """Run one turn.

        ``reply_send`` gets the model output (delta/assistant/done/error);
        ``tool_send`` gets browser tool_call frames (→ the extension that runs
        DOM ops); ``approval_send`` gets approval prompts (→ wherever the user
        confirms). All default to the host stdout sink (the side-panel case).
        For CLI-initiated turns the control server points reply/approval at the
        CLI socket while tool_send stays on stdout so the extension still acts.
        """
        reply_send = reply_send or self._host.send
        tool_send = tool_send or self._host.send
        approval_send = approval_send or self._host.send

        turn_id = str(msg.get("id") or "")
        text = str(msg.get("text") or "")
        requested_model = msg.get("model") or None

        try:
            model_name = resolve_model_name(requested_model)
        except ChatError as e:
            reply_send(P.error(id=turn_id, message=str(e)))
            reply_send(P.done(id=turn_id, stopped="error"))
            return

        self.model_name = model_name
        session = self._ensure_session(model_name)
        session.add_user(text)

        bridge = BrowserBridge(tool_send, approval_send=approval_send, turn_id=turn_id)
        self._host.pending_router = bridge
        ctx = self._build_context(bridge)
        registry = self.build_registry()

        try:
            result = run_agent_turn(
                session=session,
                ctx=ctx,
                registry=registry,
                model_name=model_name,
                stream=True,
                on_content_delta=lambda t: reply_send(
                    P.delta(id=turn_id, kind="content", text=t)
                ),
                on_reasoning_delta=lambda t: reply_send(
                    P.delta(id=turn_id, kind="reasoning", text=t)
                ),
                on_tool_call=self._log_tool_call,
                # Seamless compaction notices (context pressure / overflow
                # recovery) surface in the panel's activity rail, not the chat.
                on_system=lambda t: reply_send(P.notice(id=turn_id, text=t)),
            )
        finally:
            self._host.pending_router = None

        if result.stopped_reason == "provider_error" and result.error is not None:
            reply_send(P.error(id=turn_id, message=_format_provider_error(result.error)))
            reply_send(P.done(id=turn_id, stopped="provider_error"))
            return

        final = result.final_response
        self._record_usage(final)
        content = (final.content if final else "") or ""
        reply_send(P.assistant(id=turn_id, content=content))
        reply_send(P.done(id=turn_id, stopped=result.stopped_reason))
        # Rail state refresh after every turn: cache hit rate + todos.
        self._send_state(turn_id)


def _format_provider_error(e: ProviderError) -> str:
    parts = [e.message]
    if e.status_code:
        parts.append(f"(HTTP {e.status_code})")
    if e.hint:
        parts.append(f"\n提示: {e.hint}")
    return " ".join(parts)


__all__ = ["SessionRunner"]
