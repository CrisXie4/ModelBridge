"""Per-model context window lookup + session-level token accounting.

Values reflect each provider's public docs / model cards as of 2026-07.
Lookup falls back to a longest-prefix match before defaulting, so
unreleased variants under the same family (e.g. ``deepseek-v4-pro-preview``)
inherit the family's window.

Users can always override per model in ``models.yaml`` ::

    extra:
      context_window: 200000

NOTE: this is the *token-window* side of the ``context`` package — how many
tokens a model can accept. The sibling :mod:`modelbridge.context.budget`
handles the *char-budget* side — how much file text to paste into the prompt
before truncating. Neither deals with monetary spend (the cost-budget
sub-package was removed in 2026-07).
"""

from __future__ import annotations

import json
from typing import Iterable

from ..cost.estimator import estimate_tokens
from ..models import ModelEntry
from ..schemas import ChatMessage, text_of


# Tokens. **Per-provider values, sourced from each vendor's docs as of 2026-07.**
# Cross-checked via ModelScope API where available; see per-entry comments.
#
# DeepSeek (V4 era; V3 + chat/reasoner EOL'd 2026-07-24, removed)
#   - V4 family: 1M
# 腾讯混元
#   - Hy3 preview: 256K
# Qwen / 阿里云百炼
#   - qwen-plus / 3.6-plus: 1M
#   - qwen-max / qwen3-max / qwen3.7-max: 256K  (3.7-Max advertises 1M; see comment)
#   - qwen3-coder-* : 1M
#   - qwen-turbo: 1M
#   - qwen-long: 10M (long-context variant)
# Kimi / Moonshot (K2.x era; K2 base EOL'd 2026-05-25, removed)
#   - kimi-k2.5 / k2.6 / k2-thinking / k2-thinking-turbo / k2.7-code: 256K
# MiMo / 小米 (released 2026-03)
#   - mimo-v2 / v2.5 / v2.5-pro family: 1M (v2-omni 256K, v2-tts 128K)
# GLM / 智谱 (5.x era; old 4-plus/4-flash/4-flashx/4.5/z1 removed)
#   - glm-5.2: 1M (2026-Q2 flagship)
#   - glm-5.1 / glm-5 / 4.7 / 4.6: 200K
#   - glm-4-long: 1M
# MiniMax (M3 era; M2 + abab6.5 + 01 removed)
#   - minimax-m3 / m2.7: 1M
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    # DeepSeek (V4 era)
    "deepseek-v4":             1_000_000,
    "deepseek-v4-pro":         1_000_000,
    "deepseek-v4-flash":       1_000_000,

    # 腾讯混元
    "hy3-preview":               262_144,    # "Hy3 preview" 256K context

    # Qwen / DashScope 百炼
    "qwen-plus":               1_000_000,
    "qwen-plus-latest":        1_000_000,
    "qwen3-plus":              1_000_000,
    "qwen3.6-plus":            1_000_000,
    "qwen-max":                  262_144,
    "qwen-max-latest":           262_144,
    "qwen3-max":                 262_144,
    "qwen3.7-max":             1_000_000,    # vendor sheet advertises 1M
    "qwen-turbo":              1_000_000,
    "qwen-turbo-latest":       1_000_000,
    "qwen3-coder-plus":        1_000_000,
    "qwen3-coder-flash":       1_000_000,
    "qwen-long":              10_000_000,    # 10M long-context variant

    # Kimi / Moonshot (K2.x era)
    "kimi-k2.5":                 262_144,
    "kimi-k2.6":                 262_144,
    "kimi-k2-thinking":          262_144,
    "kimi-k2-thinking-turbo":    262_144,
    "kimi-k2.7-code":            262_144,    # 2026-06 release; "256K context" per ModelScope README

    # MiMo / 小米
    "mimo-v2":                 1_000_000,
    "mimo-v2-pro":             1_000_000,
    "mimo-v2.5-pro":           1_000_000,    # vendor sheet: 1M (flat-rate billing)
    "mimo-v2.5":               1_000_000,
    "mimo-v2-omni":              262_144,
    "mimo-v2-tts":               131_072,

    # GLM / 智谱
    "glm-5.2":                 1_000_000,    # 2026-Q2 release; "Solid 1M Context" per ModelScope README
    "glm-5.1":                   200_000,    # vendor sheet: 200K
    "glm-5":                     200_000,
    "glm-4.7":                   200_000,
    "glm-4.6":                   200_000,
    "glm-4-long":              1_000_000,

    # MiniMax (M3 era)
    "minimax-m3":              1_000_000,    # 2026-06 release; "native multimodal with 1M context" per ModelScope
    "minimax-m2.7":            1_000_000,
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
