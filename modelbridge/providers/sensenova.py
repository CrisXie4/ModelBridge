"""商汤日日新 (SenseNova) adapter.

SenseTime exposes an OpenAI-compatible mode at
``https://api.sensenova.cn/compatible-mode/v1`` with ``Authorization:
Bearer`` API keys (the native ``/v1`` API uses JWT signing — avoid it).
Model ids use the ``SenseNova-*`` spelling on the wire.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class SenseNovaProvider(OpenAICompatibleProvider):
    name = "sensenova"
    provider_type = ProviderType.SENSENOVA

    def chat_endpoint(self) -> str:
        # Guard against the native JWT-signed /v1 endpoint, which the
        # OpenAI transport cannot authenticate against.
        base = self.entry.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/compatible-mode/v1"):
            return f"{base}/chat/completions"
        if base.endswith("/compatible-mode"):
            return f"{base}/v1/chat/completions"
        if "api.sensenova.cn" in base and "/compatible-mode" not in base:
            return "https://api.sensenova.cn/compatible-mode/v1/chat/completions"
        if not base.endswith("/v1"):
            return f"{base}/v1/chat/completions"
        return f"{base}/chat/completions"

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 403, 404):
            err.hint = (
                "商汤日日新 常见排查：\n"
                "  • base_url 用 OpenAI 兼容端点 https://api.sensenova.cn/compatible-mode/v1\n"
                "    (原生 /v1 是 JWT 签名，不能直接 Bearer)；\n"
                "  • model id 官方写法为 SenseNova-V5-Turbo 等；\n"
                "  • API Key 在日日新控制台创建，Bearer 鉴权。\n"
                + (err.hint or "")
            )
        return err
