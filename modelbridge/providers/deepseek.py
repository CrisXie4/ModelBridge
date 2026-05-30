"""DeepSeek adapter.

DeepSeek (https://api.deepseek.com) is OpenAI-compatible and the home of
``deepseek-reasoner``, which surfaces ``reasoning_content`` on assistant
messages. The default ``BaseProvider`` already preserves that field on
both serialisation and parsing, so this adapter mostly customises the
endpoint normalisation and error hints.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


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

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code == 400:
            err.hint = (
                "DeepSeek 400 常见原因：\n"
                "  • 使用了 deepseek-reasoner 但 messages 中包含了非 user / assistant 的不合规字段；\n"
                "  • 历史 assistant 消息丢失了 reasoning_content (reasoner 多轮必须保留)；\n"
                "  • 模型名拼写错误，正确的是 deepseek-chat / deepseek-reasoner；\n"
                "  • temperature / response_format 与所选模型不兼容。\n"
                + (err.hint or "")
            )
        return err
