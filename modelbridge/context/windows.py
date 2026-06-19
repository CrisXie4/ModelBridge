"""Per-model context window lookup + session-level token accounting.

Values reflect each provider's public docs / model cards as of 2026-05.
Lookup falls back to a longest-prefix match before defaulting, so
unreleased variants under the same family (e.g. ``deepseek-v4-pro-preview``)
inherit the family's window.

Users can always override per model in ``models.yaml`` ::

    extra:
      context_window: 200000

NOTE: this is the *token-window* side of the ``context`` package — how many
tokens a model can accept. The sibling :mod:`modelbridge.context.budget`
handles the *char-budget* side — how much file text to paste into the prompt
before truncating. Neither is related to :mod:`modelbridge.cost.budget`, which
tracks money spent.
"""

from __future__ import annotations

import json
from typing import Iterable

from ..cost.estimator import estimate_tokens
from ..models import ModelEntry
from ..schemas import ChatMessage, text_of


# Tokens. **Per-provider values, sourced from each vendor's docs (2026-05).**
#
# DeepSeek
#   - legacy `deepseek-chat` / `deepseek-reasoner` (retiring 2026-07-24): 64K input
#   - V4 family (`deepseek-v4-*`): 1M
# Qwen / 阿里云百炼
#   - qwen-plus / 3.6-plus: 1M
#   - qwen-max / qwen3-max: 256K
#   - qwen3-coder-* : 256K native, 1M extended
#   - qwen-turbo: 1M
# Kimi / Moonshot
#   - kimi-k2.x family (k2 retiring 2026-05-25, use k2.6): 256K
#   - moonshot-v1-{8k,32k,128k}: per the name
# MiMo / 小米 (released 2026-03)
#   - mimo-v2-pro / v2.5-pro: 1M
#   - mimo-v2-omni: 256K
# GLM / 智谱
#   - glm-5 / glm-4.7: 200K
#   - glm-4.5 and below: 128K
#   - glm-4-long: 1M
# MiniMax
#   - minimax-m2 / m2.7: 200K
#   - minimax-m2.5: 192K
#   - abab6.5 series: 245K
#   - minimax-01 series: 4M
# OpenAI (kept for completeness)
#   - gpt-4o / 4o-mini: 128K
#   - gpt-4.1 family: ~1M
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    # DeepSeek
    "deepseek-chat":              64_000,
    "deepseek-reasoner":          64_000,
    "deepseek-v3":               128_000,
    "deepseek-v3.1":             128_000,
    "deepseek-v3.2":             128_000,
    "deepseek-v4":             1_000_000,
    "deepseek-v4-pro":         1_000_000,
    "deepseek-v4-flash":       1_000_000,

    # Qwen / DashScope 百炼
    "qwen-plus":               1_000_000,
    "qwen-plus-latest":        1_000_000,
    "qwen3-plus":              1_000_000,
    "qwen3.6-plus":            1_000_000,
    "qwen-max":                  262_144,
    "qwen-max-latest":           262_144,
    "qwen3-max":                 262_144,
    "qwen-turbo":              1_000_000,
    "qwen-turbo-latest":       1_000_000,
    "qwen3-coder-plus":        1_000_000,
    "qwen3-coder-flash":       1_000_000,

    # Kimi / Moonshot
    "kimi-k2":                   256_000,        # legacy K2 series — retiring 2026-05-25
    "kimi-k2.5":                 262_144,
    "kimi-k2.6":                 262_144,
    "kimi-k2-thinking":          262_144,
    "kimi-k2-thinking-turbo":    262_144,
    "kimi-k2-turbo-preview":     262_144,
    "kimi-k2-0905-preview":      262_144,
    "moonshot-v1-8k":              8_192,
    "moonshot-v1-32k":            32_768,
    "moonshot-v1-128k":          131_072,
    "moonshot-v1-auto":          131_072,

    # MiMo / 小米
    "mimo-v2":                 1_000_000,
    "mimo-v2-pro":             1_000_000,
    "mimo-v2.5-pro":           1_000_000,
    "mimo-v2.5":               1_000_000,
    "mimo-v2-omni":              262_144,
    "mimo-v2-tts":               131_072,

    # GLM / 智谱
    "glm-5":                     200_000,
    "glm-4.7":                   200_000,
    "glm-4.6":                   200_000,
    "glm-4.5":                   131_072,
    "glm-4-plus":                131_072,
    "glm-4-flash":               131_072,
    "glm-4-flashx":              131_072,
    "glm-4-long":              1_000_000,
    "glm-z1-flash":              131_072,

    # MiniMax
    "minimax-m2":                200_000,
    "minimax-m2.5":              192_000,
    "minimax-m2.7":              200_000,
    "abab6.5-chat":              245_000,
    "abab6.5s-chat":             245_000,
    "minimax-01":              4_000_000,

    # OpenAI (kept for reference)
    "gpt-4o":                    128_000,
    "gpt-4o-mini":               128_000,
    "gpt-4.1":                 1_047_576,
    "gpt-4.1-mini":            1_047_576,
    "gpt-4.1-nano":            1_047_576,
}

DEFAULT_LOCAL_WINDOW = 8_192        # most quantized local models
DEFAULT_UNKNOWN_WINDOW = 32_768     # safe assumption for unknown cloud models


def context_window_for(entry: ModelEntry) -> int:
    """Return the context-window size (in tokens) for ``entry``.

    Resolution order:

    1. ``models.yaml`` ``extra.context_window`` (explicit override).
    2. Exact match in :data:`DEFAULT_CONTEXT_WINDOWS`.
    3. **Longest-prefix match** — so ``deepseek-v4-pro-preview-1106`` still
       inherits ``deepseek-v4-pro``'s 1M window without needing to enumerate
       every variant.
    4. ``DEFAULT_LOCAL_WINDOW`` for local models.
    5. ``DEFAULT_UNKNOWN_WINDOW`` otherwise.
    """
    override = (entry.extra or {}).get("context_window")
    if isinstance(override, (int, float)) and override > 0:
        return int(override)

    mid = (entry.model or "").lower()
    if not mid:
        return DEFAULT_UNKNOWN_WINDOW

    table = {k.lower(): v for k, v in DEFAULT_CONTEXT_WINDOWS.items()}

    if mid in table:
        return table[mid]

    # Longest-prefix fallback: walk known keys by descending length.
    for key in sorted(table, key=len, reverse=True):
        if mid.startswith(key):
            return table[key]

    if entry.capabilities.local:
        return DEFAULT_LOCAL_WINDOW
    return DEFAULT_UNKNOWN_WINDOW


# ---------------------------------------------------------------------------
# Session token accounting
# ---------------------------------------------------------------------------

# Each chat message has a small structural overhead on the wire (role tag,
# JSON braces, etc.). 4 tokens is the canonical OpenAI cookbook number.
_PER_MESSAGE_OVERHEAD = 4


def estimate_message_tokens(m: ChatMessage) -> int:
    """Cheap token estimate for one :class:`ChatMessage`."""
    total = _PER_MESSAGE_OVERHEAD
    if m.content:
        total += estimate_tokens(text_of(m.content))
    if m.reasoning_content:
        total += estimate_tokens(m.reasoning_content)
    if m.tool_calls:
        try:
            total += estimate_tokens(json.dumps(m.tool_calls, ensure_ascii=False))
        except (TypeError, ValueError):
            pass
    if m.name:
        total += estimate_tokens(m.name)
    if m.tool_call_id:
        total += 4  # short id token cost
    return total


def estimate_session_tokens(messages: Iterable[ChatMessage]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def estimate_reasoning_tokens(messages: Iterable[ChatMessage]) -> int:
    """How many tokens of reasoning_content live in the session so far."""
    return sum(
        estimate_tokens(m.reasoning_content)
        for m in messages
        if m.reasoning_content
    )
