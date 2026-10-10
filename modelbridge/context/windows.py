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
from ..model_catalog import MODEL_CATALOG
from ..models import ModelEntry
from ..schemas import ChatMessage, text_of


# Tokens. 数据源已收敛到 :mod:`modelbridge.model_catalog`（E2）—— 下面的
# dict 是目录（context window / reasoning 档位 / 输入格式 / vision·pdf 能力）
# 的派生视图，不再是独立数据表；窗口事实请改目录。解析（精确 →
# longest-prefix → 本地/未知兜底）与旧硬编码表行为逐条等价；官方 model id
# 大小写变体（MiniMax-M3 等）由大小写不敏感匹配天然覆盖。
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    e.pattern: e.context_window for e in MODEL_CATALOG if not e.regex
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


def estimate_tokens_by_role(messages: Iterable[ChatMessage]) -> dict[str, int]:
    """Break session tokens down by purpose.

    Returns ``{"prefix": ..., "reasoning": ..., "tool": ..., "conversation": ...}``.

    Classification (mutually exclusive, sum ≤ ``estimate_session_tokens``):

    * ``prefix``        — the system message (system prompt + rules + project
                          summary + files concatenated by PromptBuilder).
    * ``reasoning``     — ``reasoning_content`` on any message (thinking-model
                          traces; counted separately from visible content).
    * ``tool``          — ``role == "tool"`` message bodies (tool results) and
                          ``tool_calls`` JSON on assistant messages.
    * ``conversation``  — visible ``content`` of non-system, non-tool messages
                          (user + assistant turns the user actually sees).

    Per-message overhead (4 t) is folded into ``conversation`` (or ``prefix``
    when the message is the system message) so the four buckets still sum to
    ≤ the full-session estimate.
    """
    buckets = {"prefix": 0, "reasoning": 0, "tool": 0, "conversation": 0}
    for m in messages:
        try:
            if m.role == "system":
                if m.content:
                    buckets["prefix"] += estimate_tokens(text_of(m.content)) + _PER_MESSAGE_OVERHEAD
                continue
            if m.reasoning_content:
                buckets["reasoning"] += estimate_tokens(m.reasoning_content)
            tool_payload = 0
            if m.role == "tool":
                if m.content:
                    tool_payload += estimate_tokens(text_of(m.content))
            if m.tool_calls:
                try:
                    tool_payload += estimate_tokens(json.dumps(m.tool_calls, ensure_ascii=False))
                except (TypeError, ValueError):
                    pass
            if tool_payload:
                buckets["tool"] += tool_payload + _PER_MESSAGE_OVERHEAD
                continue
            # Pure conversation message (user or assistant without tool calls).
            if m.content:
                buckets["conversation"] += estimate_tokens(text_of(m.content)) + _PER_MESSAGE_OVERHEAD
        except (TypeError, ValueError, AttributeError):
            # Never let a single malformed message break the panel.
            continue
    return buckets
