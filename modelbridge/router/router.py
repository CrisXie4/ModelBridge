"""High-level routing entry point: prompt → (TaskProfile, level, model, trace).

The router applies three layers in order:

1. :func:`classify_task` — pure-text classifier returns a
   :class:`TaskProfile` with the suggested level.
2. Mode bias — ``economy`` / ``balanced`` / ``powerful`` shift the level
   up or down a notch (e.g. economy nudges a code task toward cheap,
   powerful nudges it toward agent).
3. :func:`resolve_with_fallback` — walks ``routing.levels`` until it
   finds a model that's actually configured in ``models.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..config import load_app_config
from ..models import ModelLevel
from .classifier import RoutingDecision, TaskProfile, classify_task
from .fallback import FallbackResult, resolve_with_fallback
from .llm_classifier import classify_task_llm


RoutingMode = Literal["economy", "balanced", "powerful"]


# ---------------------------------------------------------------------------
# Mode bias — by *task type* + base level, return how many steps to shift.
# Positive = up (stronger / more expensive); negative = down (cheaper).
#
# Rationale per task type:
#   - economy:  -1 for everything except security (always keep risk safe).
#   - balanced: no shift (the classifier already targets balanced).
#   - powerful: +1 for code/agent/refactor; +2 for architecture; no
#               shift for trivial explain (no point burning expert on
#               "what is a list").
# ---------------------------------------------------------------------------

_MODE_SHIFTS: dict[str, dict[str, int]] = {
    "economy": {
        "chat":             -1,
        "explain":          -1,
        "code_explain":     -1,
        "code_generate":    -1,
        "code_edit":        -1,
        "debug":            -1,
        "architecture":     -1,
        "refactor":         -1,
        "agent_task":       -1,
        "security_review":   0,  # never drop security below expert
        "unknown":          -1,
    },
    "balanced": {},  # all 0
    "powerful": {
        "chat":              0,
        "explain":           0,
        "code_explain":     +1,
        "code_generate":    +1,
        "code_edit":        +1,
        "debug":            +1,
        "architecture":     +2,
        "refactor":         +1,
        "agent_task":       +1,
        "security_review":   0,  # already expert
        "unknown":          +1,
    },
}


def _resolve_mode(explicit: str | None) -> RoutingMode:
    if explicit:
        m = explicit.lower().strip()
        if m in ("economy", "balanced", "powerful"):
            return m  # type: ignore[return-value]
    try:
        cfg = load_app_config()
        m = (cfg.routing.mode or "balanced").lower().strip()
        if m in ("economy", "balanced", "powerful"):
            return m  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 - tolerate any config error
        pass
    return "balanced"


def apply_mode(
    profile: TaskProfile, mode: RoutingMode
) -> tuple[ModelLevel, str | None]:
    """Return ``(adjusted_level, note)`` after applying mode bias."""
    shift = _MODE_SHIFTS.get(mode, {}).get(profile.task_type, 0)
    if shift == 0:
        return profile.recommended_level, None
    from .classifier import _bump_level  # local import avoids cycle

    new_level = _bump_level(profile.recommended_level, shift)
    if new_level == profile.recommended_level:
        return new_level, None
    direction = "up" if shift > 0 else "down"
    note = (
        f"mode={mode}: shift {direction} "
        f"{profile.recommended_level.value}→{new_level.value}"
    )
    return new_level, note


@dataclass
class RouteResult:
    """Everything ``mbridge route`` and the request layer need."""

    prompt: str
    profile: TaskProfile
    decision: RoutingDecision  # legacy mirror of profile
    mode: RoutingMode
    mode_note: str | None
    fallback: FallbackResult

    @property
    def level(self) -> ModelLevel:
        # The level after mode shift — the one we tried to look up.
        if self.fallback.chain and self.fallback.chain[0][0] is not None:
            return self.fallback.chain[0][0]
        return self.profile.recommended_level

    @property
    def chosen_model(self) -> str | None:
        return self.fallback.chosen_model

    @property
    def chosen_level(self) -> ModelLevel | None:
        return self.fallback.chosen_level


def route(
    prompt: str,
    *,
    mode: str | None = None,
    has_files: bool = False,
    wants_edit: bool = False,
    wants_tools: bool = False,
    wants_mcp: bool = False,
    context_tokens: int = 0,
    previous_failures: int = 0,
    use_llm: bool = False,
) -> RouteResult:
    """Classify ``prompt`` and resolve to a concrete model.

    When ``use_llm`` is True the lowest-tier model classifies the prompt
    (:func:`classify_task_llm`); it raises :class:`LLMClassifyError` on
    failure rather than silently dropping to the keyword classifier.
    Default stays keyword-based so existing callers are unaffected.
    """
    classify = classify_task_llm if use_llm else classify_task
    profile = classify(
        prompt,
        has_files=has_files,
        wants_edit=wants_edit,
        wants_tools=wants_tools,
        wants_mcp=wants_mcp,
        context_tokens=context_tokens,
        previous_failures=previous_failures,
    )
    resolved_mode = _resolve_mode(mode)
    adjusted_level, mode_note = apply_mode(profile, resolved_mode)
    if mode_note:
        profile.reasons.append(mode_note)

    fb = resolve_with_fallback(adjusted_level)
    decision = RoutingDecision(
        level=adjusted_level,
        reasons=list(profile.reasons),
        matched_keywords=list(profile.matched_keywords),
        length=profile.length,
    )
    return RouteResult(
        prompt=prompt,
        profile=profile,
        decision=decision,
        mode=resolved_mode,
        mode_note=mode_note,
        fallback=fb,
    )
