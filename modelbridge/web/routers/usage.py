"""Cost estimation + cache stats."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...cache.manager import load_cache_stats
from ...config import find_model
from ...cost.estimator import PricingNotFound, estimate_cost, estimate_tokens

router = APIRouter(prefix="/usage", tags=["usage"])


class CostRequest(BaseModel):
    model: str
    prompt: str = ""


@router.post("/cost")
def cost_estimate(req: CostRequest) -> dict:
    entry = find_model(req.model)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"模型 '{req.model}' 不在 models.yaml 中"
        )
    input_tokens = estimate_tokens(req.prompt)
    try:
        est = estimate_cost(entry, prompt=req.prompt)
    except PricingNotFound as e:
        return {
            "ok": False,
            "error": str(e),
            "input_tokens": input_tokens,
        }
    return {
        "ok": True,
        "model": req.model,
        "input_tokens": est.input_tokens,
        "output_tokens": est.output_tokens,
        "estimated_cost": est.cost,
        "currency": est.currency,
        "pricing_source": est.pricing.source,
        "input_per_1m": est.pricing.input_per_1m,
        "output_per_1m": est.pricing.output_per_1m,
    }


@router.get("/cache")
def cache_stats() -> dict:
    stats = load_cache_stats()
    per_model = {
        name: {
            "hits": int(m.get("hits", 0)),
            "misses": int(m.get("misses", 0)),
            "saved_tokens": int(m.get("saved_tokens", 0)),
            "saved_cost": float(m.get("saved_cost", 0.0)),
        }
        for name, m in stats.per_model.items()
    }
    for name, m in per_model.items():
        total = m["hits"] + m["misses"]
        m["hit_rate"] = (m["hits"] / total) if total else 0.0
    return {
        "strategy": stats.strategy,
        "enabled": stats.enabled,
        "hits": stats.hits,
        "misses": stats.misses,
        "saved_tokens": stats.saved_tokens,
        "estimated_savings": stats.estimated_savings,
        "currency": stats.currency,
        "last_updated": stats.last_updated,
        "hit_rate": stats.hit_rate,
        "prefix_observations": stats.prefix_observations,
        "prefix_drift_count": stats.prefix_drift_count,
        "prefix_stability": stats.prefix_stability,
        # Prefix caches are per provider+model (switching models enters a
        # fresh cache domain) — this table shows each model's own hit rate.
        "per_model": per_model,
    }
