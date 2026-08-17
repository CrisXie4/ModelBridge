"""字节豆包 (Doubao / 火山方舟 Ark) adapter.

Volcano Ark serves an OpenAI-compatible API at
``https://ark.cn-beijing.volces.com/api/v3`` with ``Authorization: Bearer``
ARK API keys. Two wire quirks worth isolating:

1. Model ids come in two spellings: stable aliases (``doubao-seed-1.6`` …)
   and inference endpoint ids (``ep-2024…``). Both pass through verbatim —
   only the 404 hint needs to explain them.
2. Seed-family models do deep thinking by default and accept the same
   top-level ``thinking: {type: enabled|disabled}`` switch as DeepSeek, so
   ``ChatRequest.thinking`` translates 1:1 (no effort grades exist).
"""

from __future__ import annotations

from typing import Any

from ..models import ProviderType
from ..schemas import ChatRequest, ProviderError
from .openai_compatible import OpenAICompatibleProvider


class DoubaoProvider(OpenAICompatibleProvider):
    name = "doubao"
    provider_type = ProviderType.DOUBAO

    def build_chat_payload(self, request: ChatRequest) -> dict[str, Any]:
        body = super().build_chat_payload(request)
        # Same priority as the DeepSeek adapter: explicit extra_body wins,
        # runtime /think signal beats static models.yaml defaults.
        runtime_extra = request.extra_body or {}
        if request.thinking is not None and "thinking" not in runtime_extra:
            enabled = bool(request.thinking)
            body["thinking"] = {"type": "enabled" if enabled else "disabled"}
        return body

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 403, 404):
            err.hint = (
                "豆包 / 火山方舟 常见排查：\n"
                "  • base_url 为 https://ark.cn-beijing.volces.com/api/v3 (注意是 api/v3)；\n"
                "  • model 填模型别名 (doubao-seed-1.6 / doubao-seed-evolving …) 或推理接入点"
                " ep-xxxx (方舟控制台在线推理页创建)；\n"
                "  • API Key 在火山方舟控制台「API Key 管理」创建，Bearer 鉴权；\n"
                "  • seed 系列默认深度思考，/think off 会发送 thinking:{type:disabled}。\n"
                + (err.hint or "")
            )
        return err
