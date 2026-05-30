"""Cache statistics for the stable-prefix request path.

* ``manager`` — persistent JSON store of hit/miss counters surfaced by
  ``mbridge cache stats``.

Note: the *active* prompt builder lives at :mod:`modelbridge.prompt`, NOT
here. An older 5-section ``PromptBuilder`` used to be exported from this
package — it has been removed in favour of the 8-section builder at
``modelbridge.prompt.builder``. Imports of the form
``from modelbridge.cache import PromptBuilder`` now raise ``ImportError``
intentionally; switch to ``from modelbridge.prompt import PromptBuilder``.
"""

from .manager import (
    CacheStats,
    extract_cache_tokens,
    get_cache_path,
    load_cache_stats,
    record_hit,
    record_miss,
    record_prefix_observation,
    reset_cache_stats,
    save_cache_stats,
)

__all__ = [
    "CacheStats",
    "get_cache_path",
    "load_cache_stats",
    "save_cache_stats",
    "extract_cache_tokens",
    "record_hit",
    "record_miss",
    "record_prefix_observation",
    "reset_cache_stats",
]
