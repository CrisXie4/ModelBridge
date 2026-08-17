"""Provider adapters.

Common surface:

* :class:`BaseProvider` / :class:`HTTPProvider`     — abstract base.
* :class:`OpenAICompatibleProvider`                 — default fallback.
* :func:`get_provider`                              — registry entry point.

Per-provider adapters live in their own modules
(``deepseek.py``, ``qwen.py``, ``kimi.py``, ``mimo.py``, ``glm.py``,
``minimax.py``, ``hunyuan.py``, ``doubao.py``, ``ernie.py``, ``spark.py``,
``stepfun.py``, ``sensenova.py``, ``ollama.py``, ``local_openai.py``).
"""

from ..schemas import ChatRequest, ChatResponse, ProviderError
from .base import BaseProvider, HTTPProvider, StreamEvent
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
from .registry import get_provider, get_provider_class
from .sensenova import SenseNovaProvider
from .spark import SparkProvider
from .stepfun import StepFunProvider

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
    "HunyuanProvider",
    "DoubaoProvider",
    "ERNIEProvider",
    "SparkProvider",
    "StepFunProvider",
    "SenseNovaProvider",
    "OllamaProvider",
    "LocalOpenAIProvider",
    "get_provider",
    "get_provider_class",
]
