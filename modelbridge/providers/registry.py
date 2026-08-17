"""Provider registry — select an adapter from a :class:`ModelEntry`."""

from __future__ import annotations

import json
import os
import threading
import warnings
from typing import TYPE_CHECKING

from ..config import models_generation
from ..models import ModelEntry, ProviderType
from .deepseek import DeepSeekProvider
from .doubao import DoubaoProvider
from .ernie import ERNIEProvider
from .glm import GLMProvider
from .hunyuan import HunyuanProvider
from .kimi import KimiProvider
from .local_openai import LocalOpenAIProvider
from .mimo import MiMoProvider
from .minimax import MiniMaxProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider
from .qwen import QwenProvider
from .sensenova import SenseNovaProvider
from .spark import SparkProvider
from .stepfun import StepFunProvider

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
    ProviderType.HUNYUAN: HunyuanProvider,
    ProviderType.DOUBAO: DoubaoProvider,
    ProviderType.ERNIE: ERNIEProvider,
    ProviderType.SPARK: SparkProvider,
    ProviderType.STEPFUN: StepFunProvider,
    ProviderType.SENSENOVA: SenseNovaProvider,
    ProviderType.OLLAMA: OllamaProvider,
    ProviderType.VLLM: LocalOpenAIProvider,
    ProviderType.LMSTUDIO: LocalOpenAIProvider,
    # 外国厂商不再提供预设 profile；仅保留枚举映射让旧配置 (provider: openai)
    # 通过通用 OpenAI-compatible 适配器继续工作。
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


# Adapter instances are cached: construction resolves the API key via
# keyring/env, which on Windows costs tens of ms — paying it once per
# model (instead of once per get_provider call, which the hot path makes
# several times per turn) removes visible REPL stutter. The cache is
# value-keyed (same entry fields → same provider) and cleared whenever
# models.yaml is (re)loaded or saved (the generation counter from
# :mod:`modelbridge.config`), so edits to the registry take effect at once.
_PROVIDER_CACHE: dict[tuple, "BaseProvider"] = {}
_PROVIDER_CACHE_GEN: int | None = None
_PROVIDER_CACHE_LOCK = threading.Lock()


def _provider_cache_key(entry: ModelEntry) -> tuple:
    try:
        extra = json.dumps(entry.extra or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        extra = repr(sorted((entry.extra or {}).items(), key=lambda kv: str(kv[0])))
    env_val = os.environ.get(entry.api_key_env, "") if entry.api_key_env else ""
    caps = entry.capabilities.model_dump()
    return (
        entry.name,
        entry.provider,
        entry.model,
        entry.base_url,
        entry.api_key,
        entry.api_key_env,
        env_val,
        extra,
        json.dumps(caps, sort_keys=True, default=str),
        entry.level,
    )


def get_provider(entry: ModelEntry) -> "BaseProvider":
    """Instantiate (or reuse) the correct adapter for a model entry."""
    global _PROVIDER_CACHE_GEN
    gen = models_generation()
    key = _provider_cache_key(entry)
    with _PROVIDER_CACHE_LOCK:
        if _PROVIDER_CACHE_GEN != gen:
            _PROVIDER_CACHE.clear()
            _PROVIDER_CACHE_GEN = gen
        cached = _PROVIDER_CACHE.get(key)
        if cached is not None:
            return cached
    cls = get_provider_class(entry.provider)
    provider = cls(entry)
    with _PROVIDER_CACHE_LOCK:
        _PROVIDER_CACHE[key] = provider
    return provider
