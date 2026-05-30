"""Routing — pick a model level / name for a given prompt.

v0.3 ships a deliberately simple rule-based classifier. It looks for
Chinese + English keyword anchors and a few structural signals (length,
file globs, code blocks). This keeps routing predictable and explainable
in ``mbridge route`` output without depending on an extra model call.

The :func:`route` entry point combines :func:`classify_level` (which
returns a :class:`RoutingDecision` with reasoning) with
``config.yaml``'s ``routing.levels`` mapping and the fallback chain.
"""

from .classifier import (
    RoutingDecision,
    TaskProfile,
    classify_level,
    classify_task,
)
from .fallback import (
    FallbackResult,
    escalate_after_failure,
    resolve_with_fallback,
)
from .llm_classifier import LLMClassifyError, classify_task_llm
from .router import RouteResult, RoutingMode, apply_mode, route

__all__ = [
    "RoutingDecision",
    "TaskProfile",
    "classify_level",
    "classify_task",
    "classify_task_llm",
    "LLMClassifyError",
    "resolve_with_fallback",
    "escalate_after_failure",
    "FallbackResult",
    "RouteResult",
    "RoutingMode",
    "apply_mode",
    "route",
]
