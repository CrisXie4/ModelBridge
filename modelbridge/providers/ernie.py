"""百度文心 (ERNIE / 千帆) adapter.

Qianfan's v2 API is OpenAI-compatible:
``https://qianfan.baidubce.com/v2`` with ``Authorization: Bearer`` API keys
(from the Qianfan console — no more AK/SK access-token dance of the v1
API). Reasoning models (``ernie-x1-*``) stream ``reasoning_content`` like
DeepSeek, which the base adapter already accumulates.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class ERNIEProvider(OpenAICompatibleProvider):
    name = "ernie"
    provider_type = ProviderType.ERNIE

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 403, 404):
            err.hint = (
                "百度千帆 / 文心 常见排查：\n"
                "  • base_url 为 https://qianfan.baidubce.com/v2 (OpenAI 兼容 v2 接口；\n"
                "    v1 的 AK/SK access_token 方式已过时)；\n"
                "  • model 例如 ernie-4.5-turbo-128k / ernie-4.0-turbo-8k /"
                " ernie-x1-turbo-32k (推理)；\n"
                "  • API Key 在千帆控制台「应用接入」创建，Bearer 鉴权；\n"
                "  • ernie-x1 推理系列返回 reasoning_content，多轮工具调用需保留。\n"
                + (err.hint or "")
            )
        return err
