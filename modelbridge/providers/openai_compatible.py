"""Generic OpenAI-compatible provider.

The bulk of the request/response logic now lives on
:class:`modelbridge.providers.base.HTTPProvider`. This file exists so
``providers/openai_compatible.py`` remains a stable import path, and
provides the fallback adapter when no provider-specific class is registered.
"""

from __future__ import annotations

from ..models import ModelEntry, ProviderType
from .base import HTTPProvider


class OpenAICompatibleProvider(HTTPProvider):
    """Default adapter used when no provider-specific class is selected.

    Every other adapter in this package subclasses this so common
    behaviour (extra_body merging, message serialization, error
    normalisation) can be tweaked in one place if needed.
    """

    name = "openai-compatible"
    provider_type = ProviderType.OPENAI_COMPATIBLE


def get_provider_for(entry: ModelEntry):
    """Backwards-compatible shim for code written against v0.1.

    New code should use :func:`modelbridge.providers.registry.get_provider`.
    """
    # Local import to avoid a circular dependency at module load time.
    from .registry import get_provider
    return get_provider(entry)
