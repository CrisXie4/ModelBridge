"""Cache stats store.

Storage: ``~/.modelbridge/cache.json`` ::

    {
      "strategy": "stable-prefix",
      "enabled": true,
      "hits": 0,
      "misses": 0,
      "saved_tokens": 0,
      "estimated_savings": 0.0,
      "currency": "CNY",
      "per_model": {"<model name>": {"hits": 0, "misses": 0, ...}},
      "last_updated": "2026-05-22T14:30:00"
    }

``record_hit`` / ``record_miss`` are wired into the REPL + ask + route
request paths (provider-reported usage), with a per-model breakdown —
prefix caches are per provider+model, so that table is what makes model
switches observable. Loads/saves are mtime-cached in memory; the write is
atomic but not fsynced (it fires several times per agent turn).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import load_app_config
from ..utils import atomic_write_text, get_app_dir, now_iso

CACHE_FILE_NAME = "cache.json"


def get_cache_path() -> Path:
    return get_app_dir() / CACHE_FILE_NAME


@dataclass
class CacheStats:
    strategy: str = "stable-prefix"
    enabled: bool = True
    hits: int = 0
    misses: int = 0
    saved_tokens: int = 0
    estimated_savings: float = 0.0
    currency: str = "CNY"
    last_updated: str = field(default_factory=now_iso)
    # Last-observed prompt prefix so ``mbridge cache stats`` can point at
    # the section that drifted when the cache misses. ``last_section_hashes``
    # is the per-section dict from PromptBuildResult; comparing it against
    # the current build tells you exactly which section invalidated.
    last_prefix_hash: str = ""
    last_section_hashes: dict[str, str] = field(default_factory=dict)
    last_prefix_observed_at: str = ""
    # Cumulative drift counters — incremented every time we build a
    # prompt whose prefix differs from the last one we saw.
    prefix_observations: int = 0
    prefix_drift_count: int = 0
    # Per-model hit/miss breakdown. Prefix caches are per provider+model
    # (each model is its own cache domain — switching models never carries
    # hits over), so this is the table that shows what a model switch
    # costs. Keyed by ModelEntry.name.
    per_model: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    @property
    def prefix_stability(self) -> float:
        """Fraction of observed prefixes that matched the previous one."""
        if self.prefix_observations <= 1:
            return 1.0
        # First observation has nothing to compare against, so the
        # denominator is N-1 (every observation after the first).
        same = (self.prefix_observations - 1) - self.prefix_drift_count
        return same / (self.prefix_observations - 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "enabled": self.enabled,
            "hits": self.hits,
            "misses": self.misses,
            "saved_tokens": self.saved_tokens,
            "estimated_savings": round(self.estimated_savings, 6),
            "currency": self.currency,
            "last_updated": self.last_updated,
            "last_prefix_hash": self.last_prefix_hash,
            "last_section_hashes": dict(self.last_section_hashes),
            "last_prefix_observed_at": self.last_prefix_observed_at,
            "prefix_observations": self.prefix_observations,
            "prefix_drift_count": self.prefix_drift_count,
            "per_model": {k: dict(v) for k, v in self.per_model.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CacheStats":
        return cls(
            strategy=str(d.get("strategy", "stable-prefix")),
            enabled=bool(d.get("enabled", True)),
            hits=int(d.get("hits", 0) or 0),
            misses=int(d.get("misses", 0) or 0),
            saved_tokens=int(d.get("saved_tokens", 0) or 0),
            estimated_savings=float(d.get("estimated_savings", 0.0) or 0.0),
            currency=str(d.get("currency", "CNY")).upper(),
            last_updated=str(d.get("last_updated") or now_iso()),
            last_prefix_hash=str(d.get("last_prefix_hash", "")),
            last_section_hashes=dict(d.get("last_section_hashes", {})),
            last_prefix_observed_at=str(d.get("last_prefix_observed_at", "")),
            prefix_observations=int(d.get("prefix_observations", 0) or 0),
            prefix_drift_count=int(d.get("prefix_drift_count", 0) or 0),
            per_model=_coerce_per_model(d.get("per_model")),
        )


def _coerce_per_model(raw: Any) -> dict[str, dict[str, float]]:
    """Tolerantly parse the per-model block (old cache.json lacks it)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for name, m in raw.items():
        if not isinstance(m, dict):
            continue
        out[str(name)] = {
            "hits": int(m.get("hits", 0) or 0),
            "misses": int(m.get("misses", 0) or 0),
            "saved_tokens": int(m.get("saved_tokens", 0) or 0),
            "saved_cost": float(m.get("saved_cost", 0.0) or 0.0),
        }
    return out


def load_cache_stats() -> CacheStats:
    """Read stats, seeded from ``config.yaml`` cache settings.

    Cached in memory keyed by (path, mtime, size): the record path runs
    once per assistant iteration, and re-reading the JSON each time showed
    up as REPL stutter. Our own saves refresh the key, so an external
    writer (another mbridge process) is still picked up.
    """
    global _STATS_CACHE
    path = get_cache_path()
    try:
        st = path.stat()
        key: tuple[str, int, int] | None = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None

    if _STATS_CACHE is not None and key is not None and _STATS_CACHE[0] == key:
        return _STATS_CACHE[1]

    if key is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                stats = CacheStats.from_dict(data)
                _STATS_CACHE = (key, stats)
                return stats
        except (OSError, json.JSONDecodeError):
            pass

    cfg = load_app_config()
    return CacheStats(strategy=cfg.cache.strategy, enabled=cfg.cache.enabled)


# In-memory cache of the last loaded/saved stats — see load_cache_stats().
_STATS_CACHE: tuple[tuple[str, int, int], CacheStats] | None = None


def save_cache_stats(stats: CacheStats) -> None:
    global _STATS_CACHE
    stats.last_updated = now_iso()
    # Atomic write so an interrupted save can't truncate cache.json (which
    # load would then silently discard, losing cumulative hits/savings).
    # sync=False: this fires on every cache hit/miss (multiple times per
    # agent turn) and an fsync costs ~10ms on Windows — far too much for
    # counters where losing the very last increment is acceptable.
    atomic_write_text(
        get_cache_path(),
        json.dumps(stats.to_dict(), ensure_ascii=False, indent=2),
        sync=False,
    )
    try:
        st = get_cache_path().stat()
        _STATS_CACHE = ((str(get_cache_path()), st.st_mtime_ns, st.st_size), stats)
    except OSError:
        _STATS_CACHE = None


def record_hit(
    *,
    saved_tokens: int = 0,
    saved_cost: float = 0.0,
    model: str | None = None,
) -> CacheStats:
    s = load_cache_stats()
    s.hits += 1
    s.saved_tokens += max(0, saved_tokens)
    s.estimated_savings += max(0.0, saved_cost)
    if model:
        m = s.per_model.setdefault(
            model, {"hits": 0, "misses": 0, "saved_tokens": 0, "saved_cost": 0.0}
        )
        m["hits"] += 1
        m["saved_tokens"] += max(0, saved_tokens)
        m["saved_cost"] += max(0.0, saved_cost)
    save_cache_stats(s)
    return s


def record_miss(*, model: str | None = None) -> CacheStats:
    s = load_cache_stats()
    s.misses += 1
    if model:
        m = s.per_model.setdefault(
            model, {"hits": 0, "misses": 0, "saved_tokens": 0, "saved_cost": 0.0}
        )
        m["misses"] += 1
    save_cache_stats(s)
    return s


def reset_cache_stats() -> CacheStats:
    cfg = load_app_config()
    s = CacheStats(strategy=cfg.cache.strategy, enabled=cfg.cache.enabled)
    save_cache_stats(s)
    return s


def extract_cache_tokens(usage: dict[str, Any] | None) -> tuple[int, int]:
    """Pull ``(cache_hit_tokens, cache_miss_tokens)`` from a provider usage dict.

    Different providers report prefix-cache hits differently. We probe the
    common field layouts in order and return ``(0, 0)`` when nothing is
    found (provider doesn't support cache, or this call had no cached
    prefix). Order tried:

    * **DeepSeek** — top-level ``prompt_cache_hit_tokens`` /
      ``prompt_cache_miss_tokens``.
    * **OpenAI 4o-style** — nested ``prompt_tokens_details.cached_tokens``;
      ``miss`` is inferred as ``prompt_tokens - cached_tokens``.
    * **Kimi / GLM / others** — top-level ``cached_tokens``;
      ``miss`` likewise inferred from ``prompt_tokens``.
    """
    if not isinstance(usage, dict):
        return 0, 0

    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    if hit or miss:
        return hit, miss

    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
        if cached > 0:
            prompt_total = int(usage.get("prompt_tokens") or 0)
            return cached, max(0, prompt_total - cached)

    cached_val = usage.get("cached_tokens")
    if isinstance(cached_val, int) and cached_val > 0:
        prompt_total = int(usage.get("prompt_tokens") or 0)
        return cached_val, max(0, prompt_total - cached_val)

    return 0, 0


def record_prefix_observation(
    *,
    prefix_hash: str,
    section_hashes: dict[str, str] | None = None,
) -> tuple[CacheStats, list[str]]:
    """Record that we just built a prompt with ``prefix_hash``.

    Returns ``(stats, drifted_sections)``. ``drifted_sections`` is the
    list of section names whose hash differs from the previously seen
    observation — empty on first observation or when the prefix matches.
    Use it to power ``mbridge cache stats`` 's miss-cause diagnostics.

    Cheap: just compares hashes + writes the JSON file. Safe to call
    from every chat / agent turn.
    """
    s = load_cache_stats()
    drifted: list[str] = []
    if s.last_prefix_hash and s.last_prefix_hash != prefix_hash:
        s.prefix_drift_count += 1
        if section_hashes and s.last_section_hashes:
            for name, cur in section_hashes.items():
                prev = s.last_section_hashes.get(name, "")
                if prev and prev != cur:
                    drifted.append(name)
    s.prefix_observations += 1
    s.last_prefix_hash = prefix_hash
    if section_hashes:
        s.last_section_hashes = dict(section_hashes)
    s.last_prefix_observed_at = now_iso()
    save_cache_stats(s)
    return s, drifted
