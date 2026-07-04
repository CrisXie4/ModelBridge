"""Token + cost estimation.

* :func:`estimate_tokens` — a deliberately approximate token counter for
  Chinese + English mixed text (no tiktoken dependency — keeps the
  install light). The error margin is fine for routing / budgeting.

* :func:`estimate_cost` — multiplies estimated tokens by per-model rates.
  Rates can come from four places (later overrides earlier):

  1. Built-in defaults (see :data:`DEFAULT_PRICING`) — CNY per 1M tokens,
     best-effort. **Treat them as estimates.**
  2. ``~/.modelbridge/pricing.yaml`` — user-editable overrides keyed by
     model id (provider-side, not display name).
  3. ``models.yaml`` ``extra.pricing`` block on a model entry.
  4. Explicit ``rate_override`` kwarg.

Local models (Ollama / vLLM / LM Studio / etc.) default to a 0-cost
``Pricing`` so the estimator stays useful for offline workloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..models import ModelEntry
from ..utils import get_app_dir


PRICING_FILE_NAME = "pricing.yaml"


def get_pricing_path() -> Path:
    return get_app_dir() / PRICING_FILE_NAME


class PricingNotFound(Exception):
    """Raised when we can't find pricing for a model."""


@dataclass(frozen=True)
class Pricing:
    """Per-1M token rates."""

    currency: str  # "CNY" or "USD"
    input_per_1m: float
    output_per_1m: float
    source: str = "builtin"  # builtin | pricing.yaml | models.yaml | override | local-free
    cache_hit_input_per_1m: float | None = None

    def cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.input_per_1m
            + output_tokens / 1_000_000 * self.output_per_1m
        )


# Built-in pricing — CNY per 1 000 000 tokens unless noted otherwise.
# These are *approximate*; let users override in models.yaml.
#
# 2026-07 sweep: removed OpenAI (Western) + retired models (deepseek-chat/reasoner,
# kimi-k2, moonshot-v1-*, glm-4.5/4-plus/4-flash, minimax-m2, abab6.5-chat).
# Remaining builtin entries may be stale (2026-05 baseline) — verify before
# relying on `mbridge usage cost` numbers.
#
# Currency note: OpenRouter/LiteLLM-sourced prices are USD (international rate).
# Users paying via 6哥 API / 国产渠道 typically see different (often lower) CNY
# rates; override in ~/.modelbridge/pricing.yaml if needed.
#
# Source legend:
#   builtin             — 2026-05 baseline (CNY, may be stale)
#   openrouter-2026-07  — from openrouter.ai/api/v1/models (USD)
#   litellm-2026-07     — from BerriAI/litellm model_prices_and_context_window.json (USD)
#
# Cross-source note: V4-Flash has OpenRouter $0.09/$0.18 vs LiteLLM direct
# DeepSeek $0.14/$0.28. We keep the lower (OpenRouter) as the default; users
# on 6哥 should override.
#
# Coverage: 17/31 active models in DEFAULT_CONTEXT_WINDOWS have built-in
# 2026-07 prices. Still missing:
#   - deepseek-v4 (vanilla; no LiteLLM base entry, only v4-pro/v4-flash)
#   - glm-4-long  (1M-context variant, not enumerated by either source)
DEFAULT_PRICING: dict[str, Pricing] = {
    # ---- Qwen / 百炼 (last verified 2026-05; confirm against 2026-07 rate sheet) ----
    "qwen-plus-latest":   Pricing("CNY", 0.8,  2.0,  "builtin"),
    "qwen-max-latest":    Pricing("CNY", 2.4,  9.6,  "builtin"),
    "qwen3-coder-plus":   Pricing("CNY", 4.0, 16.0, "builtin"),
    "qwen3-coder-flash":  Pricing("CNY", 1.5,  6.0,  "builtin"),
    # ---- DeepSeek (V4 era; from OpenRouter 2026-07) ----
    "deepseek-v4-pro":    Pricing("USD", 0.435, 0.87,  "openrouter-2026-07"),
    "deepseek-v4-flash":  Pricing("USD", 0.09,  0.18,  "openrouter-2026-07"),
    # ---- Kimi / Moonshot (K2.x era; from OpenRouter + LiteLLM 2026-07) ----
    "kimi-k2.5":            Pricing("USD", 0.60,  3.00,  "litellm-2026-07"),  # moonshot/kimi-k2.5
    "kimi-k2.6":            Pricing("USD", 0.95,  4.00,  "litellm-2026-07"),  # moonshot/kimi-k2.6
    "kimi-k2-thinking":     Pricing("USD", 0.60,  2.50,  "litellm-2026-07"),  # moonshot/kimi-k2-thinking
    "kimi-k2-thinking-turbo": Pricing("USD", 1.15, 8.00,  "litellm-2026-07"),  # moonshot/kimi-k2-thinking-turbo
    "kimi-k2.7-code":       Pricing("USD", 0.74,  3.50,  "openrouter-2026-07"),
    # ---- MiMo (last verified 2026-05; confirm against 2026-07 rate sheet) ----
    "mimo-v2":            Pricing("CNY", 4.0, 16.0,  "builtin"),
    # ---- GLM / 智谱 (from OpenRouter + LiteLLM 2026-07) ----
    "glm-4.6":            Pricing("USD", 0.60,  2.20,  "litellm-2026-07"),  # zai/glm-4.6
    "glm-4.7":            Pricing("USD", 0.60,  2.20,  "litellm-2026-07"),  # zai/glm-4.7
    "glm-5":              Pricing("USD", 1.00,  3.20,  "litellm-2026-07"),  # zai/glm-5
    "glm-5.2":            Pricing("USD", 0.93,  3.00,  "openrouter-2026-07"),
    # ---- MiniMax (from OpenRouter 2026-07) ----
    "minimax-m3":         Pricing("USD", 0.30,  1.20,  "openrouter-2026-07"),
}


def load_pricing_overrides() -> dict[str, Pricing]:
    """Read ``~/.modelbridge/pricing.yaml`` and return a model-id → Pricing map.

    Returns an empty dict if the file is missing or malformed (the doctor
    surfaces parse errors separately). The expected structure is ::

        pricing:
          deepseek-chat:
            input_per_1m: 0.27
            output_per_1m: 1.10
            currency: USD
            cache_hit_input_per_1m: 0.027
    """
    path = get_pricing_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    table = data.get("pricing") or data  # tolerate top-level mapping too
    if not isinstance(table, dict):
        return {}
    out: dict[str, Pricing] = {}
    for model_id, block in table.items():
        if not isinstance(block, dict):
            continue
        try:
            out[str(model_id)] = Pricing(
                currency=str(block.get("currency", "CNY")).upper(),
                input_per_1m=float(block["input_per_1m"]),
                output_per_1m=float(block["output_per_1m"]),
                source="pricing.yaml",
                cache_hit_input_per_1m=(
                    float(block["cache_hit_input_per_1m"])
                    if block.get("cache_hit_input_per_1m") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_pricing(
    entry: ModelEntry,
    *,
    rate_override: dict[str, Any] | None = None,
) -> Pricing:
    """Resolve pricing for a registered model.

    Order: explicit override → models.yaml ``extra.pricing`` →
    pricing.yaml → builtin → 0-cost for local models →
    :class:`PricingNotFound`.
    """
    if rate_override:
        return _pricing_from_dict(rate_override, source="override")

    pricing_block = (entry.extra or {}).get("pricing")
    if isinstance(pricing_block, dict):
        return _pricing_from_dict(pricing_block, source="models.yaml")

    overrides = load_pricing_overrides()
    if entry.model in overrides:
        return overrides[entry.model]
    if entry.name in overrides:
        return overrides[entry.name]

    if entry.model in DEFAULT_PRICING:
        return DEFAULT_PRICING[entry.model]

    if entry.capabilities.local:
        return Pricing("CNY", 0.0, 0.0, source="local-free")

    raise PricingNotFound(
        f"未知模型 {entry.model!r} 的价格。请在 ~/.modelbridge/pricing.yaml "
        "或 models.yaml 的 extra.pricing 中配置 input_per_1m / output_per_1m / currency。"
    )


def _pricing_from_dict(d: dict[str, Any], *, source: str) -> Pricing:
    try:
        return Pricing(
            currency=str(d.get("currency", "CNY")).upper(),
            input_per_1m=float(d["input_per_1m"]),
            output_per_1m=float(d["output_per_1m"]),
            source=source,
            cache_hit_input_per_1m=(
                float(d["cache_hit_input_per_1m"])
                if d.get("cache_hit_input_per_1m") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise PricingNotFound(
            f"pricing 字段格式错误 ({source}): 需要 input_per_1m / output_per_1m / currency。 {e}"
        ) from e


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

_ASCII_WORDISH = re.compile(r"[A-Za-z0-9_]+")
_CJK_RANGE = (
    (0x3400, 0x9FFF),    # CJK Unified Ideographs + Extension A
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x3000, 0x303F),    # CJK Symbols & Punctuation
    (0xFF00, 0xFFEF),    # Half/Fullwidth Forms
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _CJK_RANGE:
        if lo <= cp <= hi:
            return True
    return False


def estimate_tokens(text: str) -> int:
    """Cheap mixed-language token estimate.

    Rules of thumb that match observed tokeniser behaviour well enough
    for routing / budgeting:

    * Every CJK character ≈ 1 token.
    * Every ASCII "word-ish" run ≈ ``ceil(len/4)`` tokens.
    * Punctuation / whitespace ≈ 1 token per non-trivial run.

    Returns at minimum 1 for any non-empty string.
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    ascii_tokens = 0
    for m in _ASCII_WORDISH.findall(text):
        ascii_tokens += max(1, (len(m) + 3) // 4)
    # Crude punctuation/whitespace allowance
    punct = max(0, sum(1 for ch in text if not _is_cjk(ch) and not ch.isalnum()))
    punct_tokens = punct // 4
    return max(1, cjk + ascii_tokens + punct_tokens)


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------

@dataclass
class CostEstimate:
    model_name: str
    model_id: str
    pricing: Pricing
    input_tokens: int
    output_tokens: int
    cost: float

    @property
    def currency(self) -> str:
        return self.pricing.currency

    def as_str(self) -> str:
        return (
            f"{self.cost:.4f} {self.currency}"
            f" (in={self.input_tokens}t · out={self.output_tokens}t"
            f" @ {self.pricing.input_per_1m}/{self.pricing.output_per_1m} per 1M)"
        )


def estimate_cost(
    entry: ModelEntry,
    *,
    prompt: str,
    expected_output_tokens: int | None = None,
    pricing: Pricing | None = None,
) -> CostEstimate:
    """Estimate the cost of one call to ``entry`` for ``prompt``.

    If ``expected_output_tokens`` isn't given, we use the model's
    ``extra.max_tokens`` (capped at 1024) as a generous upper bound — the
    real call usually costs *less*, which is what you want for a quick
    "is this safe to send" sanity check.
    """
    p = pricing or get_pricing(entry)
    in_tokens = estimate_tokens(prompt)
    if expected_output_tokens is None:
        budget_cap = int((entry.extra or {}).get("max_tokens", 1024))
        expected_output_tokens = min(budget_cap, 1024)
    cost = p.cost(input_tokens=in_tokens, output_tokens=expected_output_tokens)
    return CostEstimate(
        model_name=entry.name,
        model_id=entry.model,
        pricing=p,
        input_tokens=in_tokens,
        output_tokens=expected_output_tokens,
        cost=cost,
    )
