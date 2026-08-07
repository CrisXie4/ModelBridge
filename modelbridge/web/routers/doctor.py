"""Health checks — global doctor + per-model connectivity tests."""

from __future__ import annotations

from fastapi import APIRouter

from ...doctor import run_global_doctor, run_model_doctor

router = APIRouter(prefix="/doctor", tags=["doctor"])


@router.get("")
def global_doctor() -> dict:
    results = run_global_doctor()
    return {
        "checks": [
            {"name": r.name, "ok": r.ok, "detail": r.detail, "hint": r.hint}
            for r in results
        ],
        "all_ok": all(r.ok for r in results),
    }


@router.post("/{model_name}")
def model_doctor(model_name: str) -> dict:
    """Run a single-model connectivity test. This makes a real API call."""
    report = run_model_doctor(model_name)
    return {
        "name": report.name,
        "provider": report.provider,
        "level": report.level,
        "chat_ok": report.chat_ok,
        "chat_latency_ms": report.chat_latency_ms,
        "has_reasoning": report.has_reasoning,
        "json_ok": report.json_ok,
        "tools_ok": report.tools_ok,
        "status": report.status,
        "hints": report.hints,
        "results": [
            {"name": r.name, "ok": r.ok, "detail": r.detail, "hint": r.hint}
            for r in report.results
        ],
    }
