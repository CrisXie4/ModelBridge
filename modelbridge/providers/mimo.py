"""MiMo (Xiaomi) adapter.

MiMo is the most quirky China-domestic provider for AI Coding Agent
workflows in v0.2's scope. The critical invariant:

    When ``thinking`` is on AND the previous assistant turn included
    ``tool_calls``, the ``reasoning_content`` from that assistant message
    MUST be re-sent verbatim, or MiMo returns 400.

ModelBridge enforces this in two ways:

1. :class:`ChatMessage` preserves ``reasoning_content`` end-to-end.
2. :meth:`OpenAICompatibleProvider._serialize_message` emits it on the
   wire when present (we never strip it).

This adapter only adds a stronger 400 hint pointing operators at exactly
that root cause, and reminds adapters not to clean assistant messages
down to ``role`` + ``content``.
"""

from __future__ import annotations

from typing import Any

from ..models import ProviderType
from ..schemas import ChatMessage, ProviderError
from .openai_compatible import OpenAICompatibleProvider


class MiMoProvider(OpenAICompatibleProvider):
    name = "mimo"
    provider_type = ProviderType.MIMO

    def _serialize_message(self, m: ChatMessage) -> dict[str, Any]:
        # Defensive: even if the caller didn't carry reasoning_content
        # through, never drop it here. (Future v0.3 routing may snapshot
        # ``raw`` and we want full fidelity.)
        msg = m.to_wire()
        if m.raw and m.role == "assistant":
            # Merge any keys the original raw had that to_wire() didn't emit.
            for k, v in m.raw.items():
                if k not in msg and v is not None:
                    msg[k] = v
        return msg

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code == 400:
            err.hint = (
                "MiMo 400 强提示：\n"
                "  ★ 如果你开启了 thinking mode，并且历史消息包含 tool_calls，\n"
                "    请确认 assistant 历史消息中的 reasoning_content 被完整保留并回传。\n"
                "  • 不要把 assistant message 清洗成只剩 role/content；\n"
                "  • model 应该是 MiMo 官方公布的模型 ID (如 mimo-v2)；\n"
                "  • 部分参数 (response_format / tool_choice) 可能不被支持。\n"
                + (err.hint or "")
            )
        return err
