"""High-level chat client used by the CLI / server.

Wraps :mod:`modelbridge.providers` so the CLI never imports HTTP code
directly. This layer is where v0.3 routing and v0.4 caching will land.
"""

from __future__ import annotations

from typing import Any

from .config import find_model, load_app_config
from .models import ModelEntry
from .providers import ProviderError, get_provider
from .schemas import ChatMessage, ChatRequest, ChatResponse


class ChatError(Exception):
    """Raised by :func:`chat_once` for any user-actionable failure."""


def resolve_model_name(requested: str | None) -> str:
    if requested:
        return requested
    cfg = load_app_config()
    if not cfg.default_model:
        raise ChatError(
            "未指定 model，且 config.yaml 也没有 default_model。"
            "运行 `mbridge init` 与 `mbridge model init`，或加 --model 参数。"
        )
    return cfg.default_model


def get_model_entry(name: str) -> ModelEntry:
    entry = find_model(name)
    if entry is None:
        raise ChatError(
            f"未找到模型 '{name}'。可用 `mbridge model list` 查看现有模型，"
            f"或 `mbridge model init` 添加新模型。"
        )
    return entry


def chat_once(
    prompt: str,
    *,
    model_name: str | None = None,
    system: str | None = None,
    timeout: float = 60.0,
    thinking: bool | None = None,
    thinking_budget: int | None = None,
    extra: dict[str, Any] | None = None,
    save_raw: bool = False,
    verbose_label: str = "chat",
) -> tuple[ModelEntry, ChatResponse]:
    """Send a single user message via the provider registry.

    Raises:
        ChatError: config / lookup issues.
        ProviderError: anything the provider layer surfaced.
    """
    name = resolve_model_name(model_name)
    entry = get_model_entry(name)

    messages: list[ChatMessage] = []
    if system:
        messages.append(ChatMessage(role="system", content=system))
    messages.append(ChatMessage(role="user", content=prompt))

    request = ChatRequest(
        model=entry.model,
        messages=messages,
        thinking=thinking,
        thinking_budget=thinking_budget,
        extra_body=extra or {},
    )

    provider = get_provider(entry)
    try:
        resp = provider.chat(
            request, timeout=timeout, save_raw=save_raw, verbose_label=verbose_label
        )
    except ProviderError:
        raise
    return entry, resp
