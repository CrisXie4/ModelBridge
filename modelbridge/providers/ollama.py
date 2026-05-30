"""Ollama adapter (local).

Ollama's OpenAI-compatible endpoint lives at ``/v1`` on the Ollama server,
e.g. ``http://127.0.0.1:11434/v1``. Capabilities vary wildly by the
underlying GGUF model: tool calls and JSON mode are unreliable on small
models, so the doctor treats those checks as best-effort and the error
hints point the operator at the local service first.
"""

from __future__ import annotations

import httpx

from ..error_hints import hint_for_exception
from ..models import ProviderType
from ..schemas import ProviderError
from .openai_compatible import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"
    provider_type = ProviderType.OLLAMA

    def health_check(self, *, timeout: float = 3.0) -> tuple[bool, str]:
        # Ollama's native health is at the *root* (no /v1).
        root = self.entry.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(root)
            if resp.status_code < 500:
                return True, f"GET {root} → {resp.status_code} ({resp.text[:30]!r})"
            return False, f"GET {root} → {resp.status_code}"
        except httpx.HTTPError as e:
            return False, hint_for_exception(e, provider=self.name)

    def normalize_error(self, *, status_code=None, body=None, exc=None) -> ProviderError:
        err = super().normalize_error(status_code=status_code, body=body, exc=exc)
        if exc is not None and err.error_type in {"connect", "timeout"}:
            err.hint = (
                "Ollama 本地连接失败，请确认：\n"
                "  1. 已经运行 `ollama serve` (或者 ollama 桌面应用)；\n"
                "  2. 已经 `ollama pull <model>`，模型 ID 与 models.yaml 中 model 字段一致；\n"
                "  3. base_url 是 http://127.0.0.1:11434/v1 (注意结尾有 /v1)。\n"
                + (err.hint or "")
            )
        elif status_code == 404:
            err.hint = (
                "Ollama 404 常见原因：模型尚未 pull、模型 ID 写错、或者忘了在 base_url 末尾加 /v1。\n"
                + (err.hint or "")
            )
        elif status_code == 400:
            err.hint = (
                "Ollama 400 常见原因：当前底层模型不支持 tools / response_format=json_object；\n"
                "建议在 models.yaml 把对应 capabilities 关掉，或换更大的 coder 模型。\n"
                + (err.hint or "")
            )
        return err
