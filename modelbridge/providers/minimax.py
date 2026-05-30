"""MiniMax adapter.

MiniMax has both domestic (``https://api.minimaxi.com/v1``) and global
(``https://api.minimax.io/v1``) OpenAI-compatible endpoints. Some models
expose ``tool_calls`` natively; others require provider-specific tool
schemas — we surface that in the 400 hint without trying to rewrite.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class MiniMaxProvider(OpenAICompatibleProvider):
    name = "minimax"
    provider_type = ProviderType.MINIMAX

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if status_code in (400, 401, 404):
            err.hint = (
                "MiniMax 常见排查：\n"
                "  • 国内 endpoint：https://api.minimaxi.com/v1\n"
                "    国际 endpoint：https://api.minimax.io/v1\n"
                "  • api_key 区分国内 / 国际账号，二者不能互用；\n"
                "  • 模型 ID 例如 minimax-m2 / abab6.5-chat；\n"
                "  • 工具调用部分模型走专门 schema，OpenAI 风格 tools 可能 400。\n"
                + (err.hint or "")
            )
        return err
