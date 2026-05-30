"""Zhipu GLM adapter.

Zhipu (智谱) exposes an OpenAI-compatible endpoint at
``https://open.bigmodel.cn/api/paas/v4``. Most chat models accept the
standard fields; tool calls and JSON mode coverage varies by model.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class GLMProvider(OpenAICompatibleProvider):
    name = "glm"
    provider_type = ProviderType.GLM

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 404):
            err.hint = (
                "智谱 GLM 常见排查：\n"
                "  • base_url 应为 https://open.bigmodel.cn/api/paas/v4；\n"
                "  • model 例如 glm-4.5、glm-4-plus、glm-4-flash；\n"
                "  • 部分模型不支持 tool_choice='required' 或 json_object 格式；\n"
                "  • 注意 API Key 区分付费 / 体验额度。\n"
                + (err.hint or "")
            )
        return err
