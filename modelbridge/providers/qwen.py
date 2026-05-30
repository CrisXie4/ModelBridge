"""Qwen / DashScope compatible-mode adapter.

DashScope's OpenAI-compatible endpoint is
``https://dashscope.aliyuncs.com/compatible-mode/v1``. Two quirks worth
isolating here:

1. ``enable_thinking`` / ``thinking_budget`` are not in the OpenAI spec —
   Qwen expects them inside the request body alongside the standard
   fields. We honour ``ChatRequest.thinking`` and ``ChatRequest.thinking_budget``
   and translate them automatically.
2. The reasoning_content stream Qwen returns in thinking mode is parsed
   for free by the base adapter; we just need to keep it on the response.
"""

from __future__ import annotations

from typing import Any

from ..models import ProviderType
from ..schemas import ChatRequest, ProviderError
from .openai_compatible import OpenAICompatibleProvider


class QwenProvider(OpenAICompatibleProvider):
    name = "qwen"
    provider_type = ProviderType.QWEN

    def build_chat_payload(self, request: ChatRequest) -> dict[str, Any]:
        body = super().build_chat_payload(request)
        # Translate request-level thinking knobs into DashScope's expected
        # body fields. We set them only when explicitly provided so we
        # don't accidentally enable thinking on a model that doesn't
        # support it (which would produce a 400).
        if request.thinking is not None:
            body["enable_thinking"] = bool(request.thinking)
        if request.thinking_budget is not None:
            body["thinking_budget"] = int(request.thinking_budget)
        return body

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 404):
            err.hint = (
                "Qwen / 百炼 常见排查：\n"
                "  • base_url 应为 https://dashscope.aliyuncs.com/compatible-mode/v1；\n"
                "  • model 是百炼侧的模型 ID (如 qwen3-coder-plus、qwen-plus-latest)；\n"
                "  • enable_thinking / thinking_budget 仅在 thinking 系列模型上有效；\n"
                "  • 阿里云 RAM 子账号需要绑定百炼访问权限。\n"
                + (err.hint or "")
            )
        return err
