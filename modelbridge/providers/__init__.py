"""Provider adapters.

Common surface:

* :class:`BaseProvider` / :class:`HTTPProvider`     — abstract base.
* :class:`OpenAICompatibleProvider`                 — default fallback.
* :func:`get_provider`                              — registry entry point.

Per-provider adapters live in their own modules
(``deepseek.py``, ``qwen.py``, ``kimi.py``, ``mimo.py``, ``glm.py``,
``minimax.py``, ``ollama.py``, ``local_openai.py``).
"""

from ..schemas import ChatRequest, ChatResponse, ProviderError
from .base import BaseProvider, HTTPProvider, StreamEvent
from .deepseek import DeepSeekProvider
from .glm import GLMProvider
from .kimi import KimiProvider
from .local_openai import LocalOpenAIProvider
from .mimo import MiMoProvider
from .minimax import MiniMaxProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider, get_provider_for
from .qwen import QwenProvider
from .registry import get_provider, get_provider_class, list_provider_classes

__all__ = [
    "BaseProvider",
    "HTTPProvider",
    "StreamEvent",
    "ChatRequest",
    "ChatResponse",
    "ProviderError",
    "OpenAICompatibleProvider",
    "DeepSeekProvider",
    "QwenProvider",
    "KimiProvider",
    "MiMoProvider",
    "GLMProvider",
    "MiniMaxProvider",
    "OllamaProvider",
    "LocalOpenAIProvider",
    "get_provider",
    "get_provider_class",
    "get_provider_for",
    "list_provider_classes",
]
