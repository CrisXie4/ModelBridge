"""The model-context package — two distinct concerns, both about *context*:

* :mod:`.budget` — *char budget*: how much file text to paste into the prompt
  before truncating (sits between file_reader and PromptBuilder).
* :mod:`.windows` — *token window*: each model's max context size + cheap
  session token accounting.

Neither deals with monetary spend (the cost-budget sub-package was removed
in 2026-07; ``cost.estimator`` only does *estimation*).
"""

from .budget import (
    DEFAULT_MAX_CONTEXT_CHARS,
    ContextPlan,
    plan,
)
from .windows import (
    DEFAULT_CONTEXT_WINDOWS,
    context_window_for,
    estimate_reasoning_tokens,
    estimate_session_tokens,
)

__all__ = [
    "DEFAULT_MAX_CONTEXT_CHARS",
    "ContextPlan",
    "plan",
    "DEFAULT_CONTEXT_WINDOWS",
    "context_window_for",
    "estimate_reasoning_tokens",
    "estimate_session_tokens",
]
