"""Kimi (Moonshot AI) adapter.

Kimi is mostly OpenAI-compatible. Thinking models (``kimi-k1-thinking``,
``moonshot-v1-...-thinking``) expose ``reasoning_content`` like DeepSeek.
Some Kimi models reject ``response_format={"type":"json_object"}`` and
some have stricter ``tool_choice`` validation; we surface that in the
400 hint rather than try to rewrite the request silently.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class KimiProvider(OpenAICompatibleProvider):
    name = "kimi"
    provider_type = ProviderType.KIMI

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code == 400:
            err.hint = (
                "Kimi / Moonshot 400 常见排查：\n"
                "  • 部分模型 (尤其 thinking 系列) 不支持 response_format=json_object；\n"
                "  • temperature 与 thinking 模型可能冲突 (thinking 通常要求 temperature=0)；\n"
                "  • tool_choice 必须是 'auto' | 'none' 或具体函数对象；\n"
                "  • 多轮中 assistant 含 tool_calls 时，reasoning_content 必须保留并回传。\n"
                + (err.hint or "")
            )
        return err
