"""Persistent budget tracker (monthly + daily).

Storage: ``~/.modelbridge/budget.json`` ::

    {
      "currency": "CNY",
      "monthly_limit": 100.0,
      "month": "2026-05",
      "spent": 12.345,
      "daily_limit": 5.0,
      "today": "2026-05-22",
      "daily_spent": 0.34,
      "warn_at_percent": 80,
      "hard_stop": false,
      "history": [
        { "ts": "2026-05-22T14:30:00", "model": "deepseek-chat",
          "cost": 0.123, "currency": "CNY" }
      ]
    }

``add_spend`` is the single write entry point; it auto-rolls over both
month and day, updates ``spent`` / ``daily_spent``, and returns flags
that the CLI can surface as warnings (``warn`` / ``over_*``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils import atomic_write_text, get_app_dir, now_iso

BUDGET_FILE_NAME = "budget.json"


def get_budget_path() -> Path:
    return get_app_dir() / BUDGET_FILE_NAME


def current_month_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m")


def current_day_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


@dataclass
class Budget:
    currency: str = "CNY"
    monthly_limit: float = 0.0  # 0 = unlimited / not set
    month: str = field(default_factory=current_month_key)
    spent: float = 0.0
    daily_limit: float = 0.0  # 0 = unlimited / not set
    today: str = field(default_factory=current_day_key)
    daily_spent: float = 0.0
    warn_at_percent: int = 80
    hard_stop: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "monthly_limit": self.monthly_limit,
            "month": self.month,
            "spent": round(self.spent, 6),
            "daily_limit": self.daily_limit,
            "today": self.today,
            "daily_spent": round(self.daily_spent, 6),
            "warn_at_percent": self.warn_at_percent,
            "hard_stop": self.hard_stop,
            "history": self.history[-200:],  # cap history growth
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Budget":
        return cls(
            currency=str(d.get("currency", "CNY")).upper(),
            monthly_limit=float(d.get("monthly_limit", 0.0) or 0.0),
            month=str(d.get("month", current_month_key())),
            spent=float(d.get("spent", 0.0) or 0.0),
            daily_limit=float(d.get("daily_limit", 0.0) or 0.0),
            today=str(d.get("today", current_day_key())),
            daily_spent=float(d.get("daily_spent", 0.0) or 0.0),
            warn_at_percent=int(d.get("warn_at_percent", 80) or 80),
            hard_stop=bool(d.get("hard_stop", False)),
            history=list(d.get("history", []) or []),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def remaining(self) -> float | None:
        """Remaining *monthly* budget, or ``None`` if no limit is set."""
        if self.monthly_limit <= 0:
            return None
        return max(0.0, self.monthly_limit - self.spent)

    @property
    def daily_remaining(self) -> float | None:
        if self.daily_limit <= 0:
            return None
        return max(0.0, self.daily_limit - self.daily_spent)

    @property
    def over_limit(self) -> bool:
        return self.monthly_limit > 0 and self.spent >= self.monthly_limit

    @property
    def over_daily(self) -> bool:
        return self.daily_limit > 0 and self.daily_spent >= self.daily_limit

    def monthly_percent(self) -> float | None:
        if self.monthly_limit <= 0:
            return None
        return self.spent / self.monthly_limit * 100.0

    def daily_percent(self) -> float | None:
        if self.daily_limit <= 0:
            return None
        return self.daily_spent / self.daily_limit * 100.0

    def rollover_if_needed(self) -> bool:
        """Roll month and/or day forward if the calendar has moved.

        Returns ``True`` if anything rolled.
        """
        changed = False
        cur_month = current_month_key()
        if cur_month != self.month:
            self.history.append({
                "ts": now_iso(),
                "event": "month_rollover",
                "previous_month": self.month,
                "previous_spent": round(self.spent, 6),
            })
            self.month = cur_month
            self.spent = 0.0
            changed = True

        cur_day = current_day_key()
        if cur_day != self.today:
            self.history.append({
                "ts": now_iso(),
                "event": "day_rollover",
                "previous_day": self.today,
                "previous_daily_spent": round(self.daily_spent, 6),
            })
            self.today = cur_day
            self.daily_spent = 0.0
            changed = True
        return changed


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def load_budget() -> Budget:
    path = get_budget_path()
    if not path.exists():
        return Budget()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return Budget()
    except json.JSONDecodeError:
        # A corrupt budget.json (e.g. a write interrupted before atomic
        # writes landed) used to silently reset spend + limits + hard_stop.
        # Preserve the bad file under .bad and warn, so a vanished hard_stop
        # is visible rather than silent. Then start fresh.
        try:
            path.replace(path.with_suffix(".json.bad"))
            logging.getLogger("modelbridge").warning(
                "budget.json 损坏，已备份为 %s 并重置；之前的预算/限额可能已丢失。",
                path.with_suffix(".json.bad"),
            )
        except OSError:
            pass
        return Budget()
    if not isinstance(data, dict):
        return Budget()
    b = Budget.from_dict(data)
    b.rollover_if_needed()
    return b


def save_budget(b: Budget) -> None:
    # Atomic write: an interrupted save must never truncate budget.json
    # (which load_budget would then treat as "no budget" → hard_stop gone).
    atomic_write_text(
        get_budget_path(),
        json.dumps(b.to_dict(), ensure_ascii=False, indent=2),
    )


def set_monthly_limit(amount: float, *, currency: str | None = None) -> Budget:
    b = load_budget()
    b.monthly_limit = max(0.0, float(amount))
    if currency:
        b.currency = currency.upper()
    save_budget(b)
    return b


def set_daily_limit(amount: float, *, currency: str | None = None) -> Budget:
    b = load_budget()
    b.daily_limit = max(0.0, float(amount))
    if currency:
        b.currency = currency.upper()
    save_budget(b)
    return b


def set_guard(
    *,
    warn_at_percent: int | None = None,
    hard_stop: bool | None = None,
) -> Budget:
    b = load_budget()
    if warn_at_percent is not None:
        b.warn_at_percent = max(0, min(100, int(warn_at_percent)))
    if hard_stop is not None:
        b.hard_stop = bool(hard_stop)
    save_budget(b)
    return b


@dataclass
class SpendOutcome:
    """Result of :func:`add_spend` — surfaced by the CLI."""

    budget: Budget
    monthly_warn: bool = False
    daily_warn: bool = False
    over_monthly: bool = False
    over_daily: bool = False
    currency_mismatch: bool = False


def add_spend(
    *, model: str, cost: float, currency: str | None = None
) -> SpendOutcome:
    """Record one call's cost and return warn / over flags.

    Cross-currency spend is logged to history but NOT summed
    (``currency_mismatch=True``) — v0.3 doesn't auto-convert.
    """
    b = load_budget()
    b.rollover_if_needed()
    entry_currency = (currency or b.currency).upper()
    entry: dict[str, Any] = {
        "ts": now_iso(),
        "model": model,
        "cost": round(float(cost), 6),
        "currency": entry_currency,
    }
    b.history.append(entry)

    mismatch = entry_currency != b.currency
    if mismatch:
        entry["note"] = "currency_mismatch_not_summed"
    else:
        b.spent += float(cost)
        b.daily_spent += float(cost)

    # Compute warn / over BEFORE save so the result reflects post-spend state.
    monthly_pct = b.monthly_percent()
    daily_pct = b.daily_percent()
    save_budget(b)

    return SpendOutcome(
        budget=b,
        monthly_warn=(
            monthly_pct is not None and monthly_pct >= b.warn_at_percent
            and not b.over_limit
        ),
        daily_warn=(
            daily_pct is not None and daily_pct >= b.warn_at_percent
            and not b.over_daily
        ),
        over_monthly=b.over_limit,
        over_daily=b.over_daily,
        currency_mismatch=mismatch,
    )


# ---------------------------------------------------------------------------
# Guard — call BEFORE issuing a request to see if we should block.
# ---------------------------------------------------------------------------

@dataclass
class GuardDecision:
    allowed: bool
    reason: str = ""


def check_guard(*, model_is_local: bool) -> GuardDecision:
    """Return ``allowed=False`` if hard_stop is on and a non-local call
    would exceed budget. Local-free models are always allowed."""
    b = load_budget()
    b.rollover_if_needed()
    if model_is_local:
        return GuardDecision(allowed=True)
    if not b.hard_stop:
        return GuardDecision(allowed=True)
    if b.over_limit:
        return GuardDecision(
            allowed=False,
            reason=f"已超出本月预算 ({b.spent:.4f}/{b.monthly_limit:.2f} {b.currency})，hard_stop=true",
        )
    if b.over_daily:
        return GuardDecision(
            allowed=False,
            reason=f"已超出今日预算 ({b.daily_spent:.4f}/{b.daily_limit:.2f} {b.currency})，hard_stop=true",
        )
    return GuardDecision(allowed=True)
