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


# Per-model thinking profiles (token budget ranges for level 1 vs level 10).
# Only models that support thinking are listed; absence is a feature, not a
# bug — it tells the REPL "this model doesn't think".
MODEL_THINKING_PROFILES: dict[str, ThinkingProfile] = {
    # ---- Kimi / Moonshot — thinking is a core feature ----
    "kimi-k2-thinking":       ThinkingProfile(min_tokens=512, max_tokens=32768, default_level=7),
    "kimi-k2-thinking-turbo": ThinkingProfile(min_tokens=512, max_tokens=32768, default_level=9),
    "kimi-k2.7-code":         ThinkingProfile(min_tokens=512, max_tokens=32768, default_level=7),
    # ---- Qwen / DashScope — supports thinking_budget ----
    "qwen3-coder-plus":       ThinkingProfile(min_tokens=512, max_tokens=16384, default_level=5),
    "qwen3-coder-flash":      ThinkingProfile(min_tokens=512, max_tokens=8192,  default_level=4),
    "qwen3-max":              ThinkingProfile(min_tokens=512, max_tokens=32768, default_level=6),
    "qwen3.6-plus":           ThinkingProfile(min_tokens=512, max_tokens=32768, default_level=6),
    "qwen-max-latest":        ThinkingProfile(min_tokens=512, max_tokens=16384, default_level=5),
    # ---- DeepSeek — V3/V4 family has reasoning_content ----
    "deepseek-v3.1":          ThinkingProfile(min_tokens=512, max_tokens=8192,  default_level=4),
    "deepseek-v4-pro":        ThinkingProfile(min_tokens=512, max_tokens=16384, default_level=5),
    # ---- MiMo / 小米 — thinking + tool_calls ----
    "mimo-v2":                ThinkingProfile(min_tokens=512, max_tokens=16384, default_level=5),
    "mimo-v2.5-pro":          ThinkingProfile(min_tokens=512, max_tokens=16384, default_level=6),
    # ---- GLM / 智谱 — 5.x series supports thinking ----
    "glm-5.2":                ThinkingProfile(min_tokens=512, max_tokens=16384, default_level=5),
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
