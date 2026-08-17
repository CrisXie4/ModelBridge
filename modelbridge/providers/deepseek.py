"""DeepSeek adapter.

DeepSeek (https://api.deepseek.com) is OpenAI-compatible. The current
lineup is the V4 family (``deepseek-v4-flash`` / ``deepseek-v4-pro``);
``deepseek-chat`` / ``deepseek-reasoner`` were retired 2026-07-24.

Wire-format rules below are cross-checked against DeepSeek's own
open-source agent harness (github.com/deepseek-ai/deepseek-harness,
``llm-deepseek`` package) and the official API docs:

* Thinking is a top-level ``thinking: {type: enabled|disabled}`` body
  field — the successor of the old chat/reasoner model-name split.
  Effort granularity is coarse: only ``high`` / ``max`` exist on the wire
  (``reasoning_effort``); "off" is expressed as ``thinking: disabled``
  and ``reasoning_effort`` is then omitted entirely.
* Assistant ``content`` must be ``""`` — never ``null``. The live API
  rejects null-content / no-tool_calls assistant messages with a 400
  ("content or tool_calls must be set"), and since the message sits
  durably in the session log, a null there bricks every later turn.
* Reasoning passback rule: ``reasoning_content`` must be replayed on
  assistant history turns that carry ``tool_calls`` (required in
  thinking mode); on tool-call-free turns the API ignores it, so we
  drop it there to save tokens.
* Empty tool output still needs *some* content — it crosses the wire as
  the literal string ``(no output)``.
"""

from __future__ import annotations

from typing import Any

from ..models import ProviderType
from ..schemas import ChatMessage, ChatRequest, ProviderError, text_of
from .openai_compatible import OpenAICompatibleProvider

# Budgets at/above a model's ThinkingProfile.max_tokens map to the ``max``
# effort; anything lower maps to ``high`` (DeepSeek has no finer grades).
_DEFAULT_EFFORT_MAX_THRESHOLD = 16384


def _effort_for_budget(model_id: str, budget: int | None) -> str | None:
    """Map a thinking token budget to DeepSeek's coarse effort scale."""
    if budget is None:
        return None
    # Local import: modelbridge.agent.__init__ pulls in the agent loop,
    # which imports this package — keep the cycle broken at module level.
    from ..agent.thinking import profile_for

    profile = profile_for(model_id)
    threshold = profile.max_tokens if profile is not None else _DEFAULT_EFFORT_MAX_THRESHOLD
    return "max" if budget >= threshold else "high"


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    provider_type = ProviderType.DEEPSEEK

    def chat_endpoint(self) -> str:
        # DeepSeek accepts both /chat/completions and /v1/chat/completions.
        # Normalise to /v1 so we share the same code paths with the rest.
        base = self.entry.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if not base.endswith("/v1"):
            return f"{base}/v1/chat/completions"
        return f"{base}/chat/completions"

    def build_chat_payload(self, request: ChatRequest) -> dict[str, Any]:
        body = super().build_chat_payload(request)
        # ``super()`` already merged models.yaml ``entry.extra`` (static
        # defaults) and per-request ``extra_body``. Priority from here on:
        # explicit ``extra_body`` > runtime thinking knobs (/think) >
        # static ``entry.extra`` — so a mid-session /think off can still
        # override a models.yaml default.
        runtime_extra = request.extra_body or {}
        if request.thinking is not None and "thinking" not in runtime_extra:
            enabled = bool(request.thinking)
            body["thinking"] = {"type": "enabled" if enabled else "disabled"}
            if not enabled:
                # 'off' never crosses the wire as reasoning_effort.
                body.pop("reasoning_effort", None)
        elif request.thinking is None and "reasoning_effort" in body:
            # A static models.yaml effort without a thinking field would
            # send mixed signals; pair it with an explicit enabled flag.
            body.setdefault("thinking", {"type": "enabled"})
        if (
            request.thinking
            and request.thinking_budget is not None
            and "reasoning_effort" not in runtime_extra
        ):
            effort = _effort_for_budget(request.model, request.thinking_budget)
            if effort is not None:
                body["reasoning_effort"] = effort
        return body

    def _serialize_message(self, m: ChatMessage) -> dict[str, Any]:
        msg = m.to_wire()
        if m.role == "assistant":
            if msg.get("content") is None:
                # Wire rule: assistant content is "" — NEVER null, not even
                # on pure tool-call or reasoning-only turns.
                msg["content"] = ""
            if m.reasoning_content is not None and not m.tool_calls:
                # Passback rule: only tool-call turns must return
                # reasoning_content; elsewhere the API ignores it.
                msg.pop("reasoning_content", None)
        elif m.role == "tool" and not text_of(m.content):
            msg["content"] = "(no output)"
        return msg

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        message = (err.message or "").lower()
        quota_markers = ("insufficient balance", "insufficient quota", "usage limit",
                         "out of credits", "余额不足")
        if any(marker in message for marker in quota_markers):
            err.hint = (
                "DeepSeek 账户余额/额度不足：请到 https://platform.deepseek.com 充值后重试"
                "（新账户通常有免费体验额度）。\n" + (err.hint or "")
            )
        elif status_code == 400 and (
            ("context" in message and "length" in message) or "maximum context" in message
        ):
            err.hint = (
                "DeepSeek 上下文超限：请压缩会话 (/compact) 或减小 max_tokens。\n"
                + (err.hint or "")
            )
        elif status_code == 400:
            err.hint = (
                "DeepSeek 400 常见原因：\n"
                "  • assistant 消息 content 为 null（必须为 \"\"，纯 tool_calls / 纯思考轮也一样）；\n"
                "  • 带工具调用的历史 assistant 轮丢失 reasoning_content（thinking 模式必须回传）；\n"
                "  • thinking / reasoning_effort 参数格式（只接受 enabled|disabled / high|max）；\n"
                "  • 模型名拼写错误，当前在售：deepseek-v4-flash / deepseek-v4-pro"
                "（deepseek-chat / deepseek-reasoner 已于 2026-07-24 下线）；\n"
                "  • temperature 超出上限或 response_format 与所选模型不兼容。\n"
                + (err.hint or "")
            )
        return err
