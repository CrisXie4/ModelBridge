"""腾讯混元 adapter.

Hunyuan exposes an OpenAI-compatible endpoint at
``https://api.hunyuan.cloud.tencent.com/v1`` with standard ``Authorization:
Bearer <API Key>`` keys (``sk-xxx`` from the Hunyuan console). This replaces
the old plan of speaking the native TC3-HMAC-signed cloud API — the compat
endpoint removes the need for request signing entirely.

Current lineup is the ``hy3`` family (GA: MoE 295B/A21B, fast/slow fused
thinking with think/no_think dual modes). The thinking mode switch is not
mapped to ``ChatRequest.thinking`` yet — model-side behaviour governs it.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class HunyuanProvider(OpenAICompatibleProvider):
    name = "hunyuan"
    provider_type = ProviderType.HUNYUAN

    def chat_endpoint(self) -> str:
        # The compat endpoint is /v1/chat/completions; be forgiving about
        # users who paste the legacy cloud-API host instead.
        base = self.entry.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if "hunyuan.tencentcloudapi.com" in base:
            # Legacy native-API host: it does NOT serve /chat/completions,
            # but rewriting the scheme would silently mis-sign anyway —
            # surface the right host in the request URL so the 404 hint
            # can point the user at the compat endpoint.
            return "https://api.hunyuan.cloud.tencent.com/v1/chat/completions"
        if not base.endswith("/v1"):
            return f"{base}/v1/chat/completions"
        return f"{base}/chat/completions"

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 404):
            err.hint = (
                "腾讯混元 常见排查：\n"
                "  • OpenAI 兼容 base_url 为 https://api.hunyuan.cloud.tencent.com/v1"
                "（不是 hunyuan.tencentcloudapi.com，后者是 TC3 签名的原生 API）；\n"
                "  • API Key 在混元控制台「API Key 管理」创建，形如 sk-xxx，直接 Bearer 鉴权；\n"
                "  • model 例如 hy3 (GA 正式版) / hy3-preview；\n"
                "  • 账户需开通混元服务并有余额或免费额度。\n"
                + (err.hint or "")
            )
        return err
