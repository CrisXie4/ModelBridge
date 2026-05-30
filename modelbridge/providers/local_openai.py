"""Local OpenAI-compatible servers (vLLM / SGLang / LM Studio / llama.cpp).

These all expose an OpenAI ``/v1/chat/completions`` endpoint but with
different quirks at the model level. We share the same adapter and only
customise the error hints so the operator knows where to look.
"""

from __future__ import annotations

from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class LocalOpenAIProvider(OpenAICompatibleProvider):
    name = "local-openai"
    provider_type = ProviderType.VLLM  # default; registry maps lmstudio/vllm here

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if exc is not None and err.error_type in {"connect", "timeout"}:
            err.hint = (
                "本地推理服务连接失败，请确认：\n"
                "  • vLLM：`python -m vllm.entrypoints.openai.api_server --model ...` 已经在跑；\n"
                "  • LM Studio：Server 标签下点了 Start Server，base_url 是 http://127.0.0.1:1234/v1；\n"
                "  • SGLang / llama.cpp：检查启动命令是否暴露了 OpenAI 兼容端口；\n"
                "  • base_url 末尾应该带 /v1。\n"
                + (err.hint or "")
            )
        elif status_code == 404:
            err.hint = (
                "本地服务 404：通常是 base_url 末尾少了 /v1，或者模型未加载 (vLLM --served-model-name 与配置不符)。\n"
                + (err.hint or "")
            )
        elif status_code == 400:
            err.hint = (
                "本地服务 400：当前模型可能不支持 tools / json mode；可以先把 capabilities 中相关项关掉。\n"
                + (err.hint or "")
            )
        return err
