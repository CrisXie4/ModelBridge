"""Cost estimation + budget tracking.

This package is intentionally independent of the provider/HTTP layer:
``estimator`` just multiplies token counts by per-model rates, and
``budget`` is a tiny persistent JSON store. ``add_spend`` records real
usage and surfaces warn / over flags through :class:`SpendOutcome`.
"""

from .budget import (
    Budget,
    GuardDecision,
    SpendOutcome,
    add_spend,
    check_guard,
    current_day_key,
    current_month_key,
    load_budget,
    save_budget,
    set_daily_limit,
    set_guard,
    set_monthly_limit,
)
from .estimator import (
    CostEstimate,
    Pricing,
    PricingNotFound,
    estimate_cost,
    estimate_tokens,
    get_pricing,
    load_pricing_overrides,
)

__all__ = [
    "Budget",
    "GuardDecision",
    "SpendOutcome",
    "load_budget",
    "save_budget",
    "set_monthly_limit",
    "set_daily_limit",
    "set_guard",
    "check_guard",
    "add_spend",
    "current_day_key",
    "current_month_key",
    "CostEstimate",
    "Pricing",
    "PricingNotFound",
    "estimate_cost",
    "estimate_tokens",
    "get_pricing",
    "load_pricing_overrides",
]
