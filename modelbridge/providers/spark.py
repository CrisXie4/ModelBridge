"""讯飞星火 (Spark) adapter.

iFlytek's Spark X series runs on the 星辰 MaaS platform with an
OpenAI-compatible endpoint at
``https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1`` (Bearer, key created in
the Spark console — the classic ``spark-api-open.xf-yun.com/v1`` endpoint
with APIPassword auth still serves the older lite / generalv3.5 / 4.0Ultra
models and works here too, just paste it as base_url).
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class SparkProvider(OpenAICompatibleProvider):
    name = "spark"
    provider_type = ProviderType.SPARK

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 403, 404):
            err.hint = (
                "讯飞星火 常见排查：\n"
                "  • X2 系列走星辰 MaaS：base_url https://xinghuo-maas.cn-huabei-1.xf-yun.com/v1；\n"
                "  • 旧款 (lite / generalv3.5 / 4.0Ultra) 走 https://spark-api-open.xf-yun.com/v1，\n"
                "    鉴权用控制台生成的 APIPassword；\n"
                "  • model 例如 x2 / x2-flash / x2-agent (MaaS) 或 4.0Ultra (旧端点)；\n"
                "  • 新用户通常有 1500 万 tokens 体验额度，Lite 永久免费。\n"
                + (err.hint or "")
            )
        return err
