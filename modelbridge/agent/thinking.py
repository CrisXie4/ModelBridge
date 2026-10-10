"""Per-model thinking profiles + intensity-level resolution.

Different models support different thinking mechanisms
(Qwen ``thinking_budget``, Kimi thinking, DeepSeek-reasoner, MiMo, GLM)
with very different useful budget ranges — a 4K budget is generous for
Qwen3-Coder but starved for Kimi-K2-Thinking. This module centralises:

* :data:`MODEL_THINKING_PROFILES` — per-model ``(min, max, default_level)``
* :func:`profile_for` — longest-prefix lookup (same idiom as
  :mod:`modelbridge.context.windows` for context windows)
* :func:`budget_for` — resolve ``(model, level)`` to a token count
* :func:`parse_level` — parse CLI args like ``7`` or ``high`` to 1-10
* :data:`NAMED_LEVELS` — ``low/med/high/xhigh`` → 1-10 aliases

Models that do not support thinking are deliberately absent from
:data:`MODEL_THINKING_PROFILES`; :func:`profile_for` returns ``None`` for
them, and the REPL ``/think on`` warns accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..model_catalog import MODEL_CATALOG


# Named-level aliases: short labels that map to intensity 1-10.
NAMED_LEVELS: dict[str, int] = {
    "low":    3,
    "med":    6,
    "high":   9,
    "xhigh": 10,
    "min":    1,
    "max":   10,
}


@dataclass(frozen=True)
class ThinkingProfile:
    """Per-model thinking budget range + default intensity level.

    ``min_tokens`` / ``max_tokens`` bound the 1-10 intensity scale
    (level 1 → ``min_tokens``, level 10 → ``max_tokens``).
    Intermediate levels are linearly interpolated.
    """

    min_tokens: int
    max_tokens: int
    default_level: int = 5  # 1-10

    def __post_init__(self) -> None:
        if self.min_tokens <= 0:
            raise ValueError(f"min_tokens must be > 0, got {self.min_tokens}")
        if self.max_tokens < self.min_tokens:
            raise ValueError(
                f"max_tokens ({self.max_tokens}) must be >= min_tokens ({self.min_tokens})"
            )
        if not 1 <= self.default_level <= 10:
            raise ValueError(f"default_level must be in 1-10, got {self.default_level}")

    def budget_for_level(self, level: int) -> int:
        """Linear interp: level 1 → ``min_tokens``, level 10 → ``max_tokens``.

        Levels outside 1-10 are clamped.
        """
        level = max(1, min(10, level))
        if level == 10:
            return self.max_tokens
        if level == 1:
            return self.min_tokens
        # Interpolate on the 9-step range [1..10].
        return self.min_tokens + (self.max_tokens - self.min_tokens) * (level - 1) // 9


# Per-model thinking profiles —— E2 起从 :mod:`modelbridge.model_catalog`
# 的 reasoning 档位派生（目录是唯一数据源）；不在目录里 = 不支持思考，
# profile_for 返回 None（这是特性：告诉 REPL 该模型不会思考）。
MODEL_THINKING_PROFILES: dict[str, ThinkingProfile] = {
    e.pattern: ThinkingProfile(
        min_tokens=e.reasoning.min_tokens,
        max_tokens=e.reasoning.max_tokens,
        default_level=e.reasoning.default_level,
    )
    for e in MODEL_CATALOG
    if e.reasoning is not None and not e.regex
}


def profile_for(model_id: str) -> Optional[ThinkingProfile]:
    """Longest-prefix lookup against :data:`MODEL_THINKING_PROFILES`.

    Resolution order:

    1. Exact match (case-insensitive).
    2. Longest-prefix match — so ``qwen3-coder-plus-2025-preview`` still
       inherits ``qwen3-coder-plus``'s profile.
    3. ``None`` if no key matches.
    """
    if not model_id:
        return None
    mid = model_id.lower()

    table = {k.lower(): v for k, v in MODEL_THINKING_PROFILES.items()}

    if mid in table:
        return table[mid]

    for key in sorted(table, key=len, reverse=True):
        if mid.startswith(key):
            return table[key]
    return None


def budget_for(model_id: str, level: int) -> Optional[int]:
    """Resolve ``(model, level)`` to a thinking budget token count.

    Returns ``None`` if the model has no profile (i.e. doesn't support
    thinking) or if the level is invalid.
    """
    profile = profile_for(model_id)
    if profile is None:
        return None
    try:
        return profile.budget_for_level(int(level))
    except (TypeError, ValueError):
        return None


def parse_level(arg: str) -> Optional[int]:
    """Parse a CLI arg like ``7``, ``high``, ``xhigh`` to a 1-10 intensity.

    Returns ``None`` if the arg can't be parsed. Named levels are
    case-insensitive (``HIGH`` ≡ ``high``).
    """
    if not arg:
        return None
    s = arg.strip().lower()
    if s in NAMED_LEVELS:
        return NAMED_LEVELS[s]
    try:
        n = int(s)
    except ValueError:
        return None
    if 1 <= n <= 10:
        return n
    return None


__all__ = [
    "NAMED_LEVELS",
    "ThinkingProfile",
    "MODEL_THINKING_PROFILES",
    "profile_for",
    "budget_for",
    "parse_level",
]
