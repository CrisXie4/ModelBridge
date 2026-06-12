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
    "- read_page: 读页面标题/URL/正文 (总结、问答前先调用)\n"
    "- get_selection: 读用户选中的文本\n"
    "- query_dom / extract: 用 CSS 选择器定位元素、提取文本或属性\n"
    "- click / fill / navigate: 操作页面 (这些会向用户请求确认)\n"
    "需要页面信息时主动调用工具，不要凭空猜测页面内容。回答要直接、可执行。\n"
    "页面正在加载时，工具会自动等到加载完成后再执行；navigate / click 触发跳转后"
    "也会等新页面加载完再返回。所以遇到加载慢的页面，继续调用工具完成任务即可，"
    "不要中途放弃、不要让用户自己去刷新或查看页面。"
)


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

    def _ensure_session(self, model_name: str) -> Session:
        if self.session is None:
            self.session = Session(model_name=model_name)
            self.session.add_system(_SYSTEM_PROMPT)
        return self.session

    def _build_context(self, bridge: BrowserBridge) -> AgentContext:
        def approve(*, tool: str, summary: str, detail: str = "") -> ApprovalDecision:
            decision = bridge.request_approval(tool=tool, summary=summary, detail=detail)
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
        )

    def build_registry(self):
        """Browser tools (read tools always; write tools added in Stage 3)."""
        return build_browser_registry(include_write=True)

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
                on_tool_call=lambda call, out: self._log.info("bridge.turn tool=%s", call.name),
            )
        finally:
            self._host.pending_router = None

        if result.stopped_reason == "provider_error" and result.error is not None:
            reply_send(P.error(id=turn_id, message=_format_provider_error(result.error)))
            reply_send(P.done(id=turn_id, stopped="provider_error"))
            return

        final = result.final_response
        content = (final.content if final else "") or ""
        reply_send(P.assistant(id=turn_id, content=content))
        reply_send(P.done(id=turn_id, stopped=result.stopped_reason))


def _format_provider_error(e: ProviderError) -> str:
    parts = [e.message]
    if e.status_code:
        parts.append(f"(HTTP {e.status_code})")
    if e.hint:
        parts.append(f"\n提示: {e.hint}")
    return " ".join(parts)


__all__ = ["SessionRunner"]
