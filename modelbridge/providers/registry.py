"""Provider registry — select an adapter from a :class:`ModelEntry`."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from ..models import ModelEntry, ProviderType
from .deepseek import DeepSeekProvider
from .glm import GLMProvider
from .kimi import KimiProvider
from .local_openai import LocalOpenAIProvider
from .mimo import MiMoProvider
from .minimax import MiniMaxProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider
from .qwen import QwenProvider

if TYPE_CHECKING:
    from .base import BaseProvider


# Provider → adapter class. ProviderType members that aren't listed fall back
# to OpenAICompatibleProvider via :func:`get_provider`.
_REGISTRY: dict[ProviderType, type["BaseProvider"]] = {
    ProviderType.DEEPSEEK: DeepSeekProvider,
    ProviderType.QWEN: QwenProvider,
    ProviderType.KIMI: KimiProvider,
    ProviderType.MIMO: MiMoProvider,
    ProviderType.GLM: GLMProvider,
    ProviderType.MINIMAX: MiniMaxProvider,
    ProviderType.OLLAMA: OllamaProvider,
    ProviderType.VLLM: LocalOpenAIProvider,
    ProviderType.LMSTUDIO: LocalOpenAIProvider,
    ProviderType.OPENAI: OpenAICompatibleProvider,
    ProviderType.OPENAI_COMPATIBLE: OpenAICompatibleProvider,
    ProviderType.CUSTOM: OpenAICompatibleProvider,
}


def get_provider_class(provider: ProviderType) -> type["BaseProvider"]:
    cls = _REGISTRY.get(provider)
    if cls is None:
        warnings.warn(
            f"unknown provider {provider!r}; falling back to OpenAICompatibleProvider",
            stacklevel=2,
        )
        return OpenAICompatibleProvider
    return cls


def get_provider(entry: ModelEntry) -> "BaseProvider":
    """Instantiate the correct adapter for a model entry."""
    cls = get_provider_class(entry.provider)
    return cls(entry)


def list_provider_classes() -> dict[str, type["BaseProvider"]]:
    """Map provider name → class (mostly for doctor / debugging)."""
    return {p.value: cls for p, cls in _REGISTRY.items()}
