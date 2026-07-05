"""Fallback resolution — when the routed model isn't usable, walk a chain.

Two different fallback flavours live here:

* :func:`resolve_with_fallback` — *static* resolution at routing time.
  We have a desired level; walk ``routing.levels`` downward until we
  find one mapped to a model that actually exists in ``models.yaml``.

* :func:`escalate_after_failure` — *runtime* escalation when a call
  fails. Walk the levels *upward* (cheap→coder→agent→expert) so a
  retry has a chance of succeeding. Capped by
  ``config.routing.fallback.max_upgrade_steps``.

Both return *names*, not provider instances — the caller can then
``find_model`` / ``get_provider`` as needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import load_app_config, load_models_file
from ..models import ModelLevel


# Downward order (for static resolution).
_LEVEL_ORDER_DOWN = [
    ModelLevel.EXPERT,
    ModelLevel.AGENT,
    ModelLevel.CODER,
    ModelLevel.CHEAP,
    ModelLevel.TINY,
]

# Upward order (for runtime escalation).
_LEVEL_ORDER_UP = [
    ModelLevel.TINY,
    ModelLevel.CHEAP,
    ModelLevel.CODER,
    ModelLevel.AGENT,
    ModelLevel.EXPERT,
]


@dataclass
class FallbackResult:
    chosen_model: str | None
    chosen_level: ModelLevel | None
    chain: list[tuple[ModelLevel | None, str | None, str]]  # (level, model, status)


def resolve_with_fallback(level: ModelLevel) -> FallbackResult:
    """Resolve ``level`` to a concrete model name, walking the chain.

    The ``chain`` field records every level we considered along with the
    raw mapping and the resolution status, so ``mbridge route`` can
    print a transparent trace.
    """
    cfg = load_app_config()
    levels_map = cfg.routing.levels.model_dump()
    # Load models.yaml once and resolve candidates in memory — find_model()
    # would re-read + re-validate the whole file for every level on the chain.
    models_by_name = {m.name: m for m in load_models_file().models}

    chain: list[tuple[ModelLevel | None, str | None, str]] = []

    # Walk from the requested level downward through the order list.
    start = _LEVEL_ORDER_DOWN.index(level)
    for cur in _LEVEL_ORDER_DOWN[start:]:
        candidate = levels_map.get(cur.value)
        if not candidate:
            chain.append((cur, None, "未在 routing.levels 中配置"))
            continue
        if candidate not in models_by_name:
            chain.append((cur, candidate, "未在 models.yaml 中找到"))
            continue
        chain.append((cur, candidate, "OK"))
        return FallbackResult(chosen_model=candidate, chosen_level=cur, chain=chain)

    # Last resort — default_model.
    if cfg.default_model and cfg.default_model in models_by_name:
        chain.append((None, cfg.default_model, "default_model 兜底"))
        return FallbackResult(
            chosen_model=cfg.default_model, chosen_level=None, chain=chain
        )
    if cfg.default_model:
        chain.append((None, cfg.default_model, "default_model 不存在于 models.yaml"))

    return FallbackResult(chosen_model=None, chosen_level=None, chain=chain)


# ---------------------------------------------------------------------------
# Runtime escalation
# ---------------------------------------------------------------------------

@dataclass
class EscalationStep:
    from_level: ModelLevel
    to_level: ModelLevel | None
    to_model: str | None
    reason: str
    note: str = ""


@dataclass
class EscalationResult:
    """Outcome of one escalation attempt after a call failure."""

    escalated: bool
    step: EscalationStep
    chain: list[EscalationStep] = field(default_factory=list)

    @property
    def chosen_model(self) -> str | None:
        return self.step.to_model

    @property
    def chosen_level(self) -> ModelLevel | None:
        return self.step.to_level


def _config_fallback_enabled(cfg: Any = None) -> bool:
    try:
        if cfg is None:
            cfg = load_app_config()
        fb = getattr(cfg.routing, "fallback", None)
        if fb is None:
            return True
        return bool(fb.enabled)
    except Exception:
        return True


def _max_upgrade_steps(cfg: Any = None) -> int:
    try:
        if cfg is None:
            cfg = load_app_config()
        fb = getattr(cfg.routing, "fallback", None)
        if fb is None:
            return 2
        return max(0, int(fb.max_upgrade_steps))
    except Exception:
        return 2


def escalate_after_failure(
    current_level: ModelLevel,
    *,
    reason: str,
    attempts_used: int = 0,
) -> EscalationResult:
    """Pick a *stronger* level after a call failed.

    ``attempts_used`` is the number of upgrade steps already taken in
    this turn (so a caller looping over failures stays within
    ``max_upgrade_steps``).
    """
    # Load config once and reuse it for both the enabled / max-steps checks
    # and the level map below (was three separate load_app_config() calls).
    try:
        cfg = load_app_config()
    except Exception:
        cfg = None
    enabled = _config_fallback_enabled(cfg)
    max_steps = _max_upgrade_steps(cfg)

    if not enabled:
        return EscalationResult(
            escalated=False,
            step=EscalationStep(
                from_level=current_level,
                to_level=None,
                to_model=None,
                reason=reason,
                note="fallback disabled in config",
            ),
        )
    if attempts_used >= max_steps:
        return EscalationResult(
            escalated=False,
            step=EscalationStep(
                from_level=current_level,
                to_level=None,
                to_model=None,
                reason=reason,
                note=f"already used {attempts_used} upgrade(s) (max {max_steps})",
            ),
        )

    if cfg is None:
        cfg = load_app_config()  # config genuinely unreadable → surface it
    levels_map = cfg.routing.levels.model_dump()
    models_by_name = {m.name: m for m in load_models_file().models}

    chain: list[EscalationStep] = []

    # Walk upward from current_level. Skip levels with no configured /
    # resolvable model — that wastes a retry budget on a known dud.
    try:
        idx = _LEVEL_ORDER_UP.index(current_level)
    except ValueError:
        idx = 0
    for cur in _LEVEL_ORDER_UP[idx + 1:]:
        candidate = levels_map.get(cur.value)
        if not candidate:
            chain.append(EscalationStep(
                from_level=current_level,
                to_level=cur,
                to_model=None,
                reason=reason,
                note="未在 routing.levels 中配置，跳过",
            ))
            continue
        if candidate not in models_by_name:
            chain.append(EscalationStep(
                from_level=current_level,
                to_level=cur,
                to_model=candidate,
                reason=reason,
                note="未在 models.yaml 中找到，跳过",
            ))
            continue
        step = EscalationStep(
            from_level=current_level,
            to_level=cur,
            to_model=candidate,
            reason=reason,
            note="OK",
        )
        chain.append(step)
        return EscalationResult(escalated=True, step=step, chain=chain)

    # Nothing usable upstream.
    return EscalationResult(
        escalated=False,
        step=EscalationStep(
            from_level=current_level,
            to_level=None,
            to_model=None,
            reason=reason,
            note="no stronger level available",
        ),
        chain=chain,
    )
