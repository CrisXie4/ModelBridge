"""阶跃星辰 (StepFun) adapter.

StepFun's platform is OpenAI-compatible at ``https://api.stepfun.com/v1``
with ``Authorization: Bearer`` keys. ``step-3`` is the omni flagship
(text+image input); the ``step-2`` family is text-only. Tool calls and
JSON mode are supported on current generations.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class StepFunProvider(OpenAICompatibleProvider):
    name = "stepfun"
    provider_type = ProviderType.STEPFUN

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 403, 404):
            err.hint = (
                "阶跃星辰 StepFun 常见排查：\n"
                "  • base_url 为 https://api.stepfun.com/v1；\n"
                "  • model 例如 step-3 (全模态旗舰) / step-2-16k / step-2-mini；\n"
                "  • API Key 在 platform.stepfun.com 创建，Bearer 鉴权；\n"
                "  • step-3 支持图像输入，step-2 系列仅文本。\n"
                + (err.hint or "")
            )
        return err
