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


# Built-in pricing — per 1 000 000 tokens, currency as noted.
# These are *approximate*; let users override in models.yaml.
#
# 2026-08 refresh: each entry below now cites the official vendor page it was
# verified against (URLs in the per-section headers). Source legend:
#   vendor-2026-08-17 — re-verified against the live vendor pricing page this sweep
#   vendor-2026-08  — re-verified against the live vendor pricing page this sweep
#   vendor-2026-07  — verified against vendor rate sheet 2026-07 (prior sweep)
#   builtin / litellm-2026-07 — pre-existing, not re-verified this sweep
#
# Vendor-page sources consulted this sweep:
#   DeepSeek : https://api-docs.deepseek.com/quick_start/pricing
#   Kimi     : https://platform.kimi.ai/docs/pricing/chat-k3
#              https://platform.kimi.ai/docs/pricing/chat-k27-code
#              https://platform.kimi.ai/docs/pricing/chat-k26
#   MiniMax  : https://platform.minimax.io/docs/guides/pricing-paygo
#              https://platform.minimax.io/docs/guides/text-generation
#   Qwen     : https://help.aliyun.com/zh/model-studio/billing-for-model-studio
#              (华北2 北京原价；OpenRouter 交叉验证 qwen3.8-max = $2/$6 ≈ ¥12/¥36)
#   MiMo     : https://mimo.mi.com/  (中国站 CNY)
#              https://mimo.xiaomi.com/mimo-v2-pro (国际站 USD, V2-Pro)
#
# Currency note: GLM-5.2 is quoted CNY (domestic 智谱), GLM-5.1 USD
# (international) — mixed currencies are NOT auto-converted, so pick one per
# model and override in ~/.modelbridge/pricing.yaml if you need comparability.
#
# MiMo-V2 sunset: 小米公告 "MiMo-V2 系列已于 2026.6.30 下线" → the legacy
# `mimo-v2` entry was removed and replaced by `mimo-v2.5` (¥1/¥2, 1M ctx).
#
# MiniMax M3 promo: official page lists "Permanent 50% off" → we record the
# current payable price ($0.30/$1.20) as the default; the pre-promo rate is
# noted in the comment for reference.
DEFAULT_PRICING: dict[str, Pricing] = {
    # ---- DeepSeek (vendor 2026-08-17 峰谷计价, CNY; api-docs.deepseek.com) ----
    # V4 全系正式版 2026-08-13 上线，新价 2026-08-17 00:00 生效。表里记录
    # 高峰时段价（北京时间 9:00-12:00 / 14:00-18:00），闲时一律半价；缓存
    # 命中输入单独计价。旧 USD 预览价 ($0.435/$0.87) 已作废。
    "deepseek-v4-pro":    Pricing("CNY", 9.0,  27.0, "vendor-2026-08-17",
                                  cache_hit_input_per_1m=0.30),   # 闲时 0.15/4.5/13.5
    "deepseek-v4-flash":  Pricing("CNY", 3.0,  9.0,  "vendor-2026-08-17",
                                  cache_hit_input_per_1m=0.10),   # 闲时 0.05/1.5/4.5

    # ---- 字节豆包 / 火山方舟 (builtin best-effort, CNY；官方价以
    #      docs.volcengine.com/docs/82379/1544106 为准) ----
    # Seed-Evolving 为最新 Coding & Agent 旗舰 (输出价 ¥30/1M 见 AI Hub)。
    "doubao-seed-evolving": Pricing("CNY", 8.0, 30.0, "builtin"),
    # 官网快照列表价；限时促销价 0.48/1.92。
    "doubao-seed-1.8":     Pricing("CNY", 5.76, 23.04, "builtin"),
    # 证券时报报道的 1.6 定价。
    "doubao-seed-1.6":     Pricing("CNY", 2.4, 24.0, "builtin"),

    # ---- 百度文心 / 千帆 (builtin best-effort, CNY；以千帆价格文档为准) ----
    "ernie-4.5-turbo-128k": Pricing("CNY", 0.8, 3.2, "builtin"),
    "ernie-4.0-turbo-8k":   Pricing("CNY", 30.0, 90.0, "builtin"),  # 2024 挂牌价
    "ernie-speed-128k":     Pricing("CNY", 0.0, 0.0, "builtin"),    # 免费档
    "ernie-x1-turbo-32k":   Pricing("CNY", 1.0, 4.0, "builtin"),    # 推理系列

    # ---- 腾讯混元 (vendor 2026-08, CNY) ----
    # hy3 正式版 GA (快慢思考融合 MoE 295B/A21B)；TokenHub 闲时半价。
    # preview 已被 GA 版替代并从价格表移除（windows 表保留兼容旧配置）。
    "hy3":                Pricing("CNY", 1.0, 4.0, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.25),

    # ---- 智谱 GLM (vendor sheet 2026-07) ----
    "glm-5.2":            Pricing("CNY", 8.0,  28.0, "vendor-2026-07"),
    "glm-5.1":            Pricing("USD", 1.00, 3.20, "vendor-2026-07"),  # input inferred

    # ---- Qwen / 百炼 (vendor 2026-08, CNY; help.aliyun.com/zh/model-studio/billing) ----
    # 阶梯计价的模型记录 ≤第一档 的价格（最常用区段）。qwen3.7-max 当前限时 5 折，
    # 这里记录原价；用户若开了促销可在 pricing.yaml 覆盖。
    "qwen3.8-max":        Pricing("CNY", 12.0, 36.0, "vendor-2026-08"),
    "qwen3.7-max":        Pricing("CNY", 12.0, 36.0, "vendor-2026-08"),
    "qwen3.7-plus":       Pricing("CNY", 2.0,  8.0,  "vendor-2026-08"),

    # ---- Kimi / Moonshot (vendor 2026-08, USD) ----
    # K3 是新一代旗舰（1M ctx，默认 reasoning），K2.7-code 是编码专用。
    "kimi-k3":            Pricing("USD", 3.00, 15.00, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.30),
    "kimi-k2.7-code":     Pricing("USD", 0.95, 4.00, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.19),
    "kimi-k2.6":          Pricing("USD", 0.95, 4.00, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.16),

    # ---- MiniMax (vendor 2026-08, USD; 国际站 platform.minimax.io) ----
    # M3 当前为官方 "Permanent 50% off" 促销价（原价 $0.60/$2.40）；
    # >512k 区段为 $0.60/$2.40（促销）/ $1.20/$4.80（原），此处只记录 ≤512k。
    "minimax-m3":         Pricing("USD", 0.30, 1.20, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.06),
    "minimax-m2.7":       Pricing("USD", 0.30, 1.20, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.06),
    "minimax-m2.5":       Pricing("USD", 0.30, 1.20, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.03),
    # 官方 model id 大小写别名（首字母大写含点）
    "MiniMax-M3":         Pricing("USD", 0.30, 1.20, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.06),
    "MiniMax-M2.7":       Pricing("USD", 0.30, 1.20, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.06),
    "MiniMax-M2.5":       Pricing("USD", 0.30, 1.20, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.03),

    # ---- 小米 MiMo (vendor 2026-08, CNY; 中国站 mimo.mi.com) ----
    # V2 系列已于 2026-06-30 下线；v2.5 是原生全模态 + 1M ctx 的入门款。
    "mimo-v2.5-pro":      Pricing("CNY", 3.0, 6.0, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.025),
    "mimo-v2.5-pro-ultraspeed": Pricing("CNY", 9.0, 18.0, "vendor-2026-08",
                                        cache_hit_input_per_1m=0.075),
    "mimo-v2.5":          Pricing("CNY", 1.0, 2.0, "vendor-2026-08",
                                  cache_hit_input_per_1m=0.02),

    # ---- Retained from earlier sweep (not on this sheet; still valid models) ----
    # Qwen / 百炼 — older tiers (prices unchanged, still callable)
    "qwen-plus-latest":   Pricing("CNY", 0.8,  2.0,  "builtin"),
    "qwen3.6-plus":       Pricing("CNY", 0.8,  2.0,  "builtin"),  # 同 plus 档
    "qwen-max-latest":    Pricing("CNY", 2.4,  9.6,  "builtin"),
    "qwen3-max":          Pricing("CNY", 2.4,  9.6,  "builtin"),  # 同 max 家族价
    "qwen3-coder-plus":   Pricing("CNY", 4.0, 16.0, "builtin"),
    "qwen3-coder-flash":  Pricing("CNY", 1.5,  6.0, "builtin"),
    # Kimi thinking family
    "kimi-k2.5":              Pricing("USD", 0.60, 3.00, "litellm-2026-07"),
    "kimi-k2-thinking":       Pricing("USD", 0.60, 2.50, "litellm-2026-07"),
    "kimi-k2-thinking-turbo": Pricing("USD", 1.15, 8.00, "litellm-2026-07"),
    # GLM older
    "glm-4.6":            Pricing("USD", 0.60, 2.20, "litellm-2026-07"),
    "glm-4.7":            Pricing("USD", 0.60, 2.20, "litellm-2026-07"),
    "glm-5":              Pricing("USD", 1.00, 3.20, "litellm-2026-07"),
}


def load_pricing_overrides() -> dict[str, Pricing]:
    """Read ``~/.modelbridge/pricing.yaml`` and return a model-id → Pricing map.

    Returns an empty dict if the file is missing or malformed (the doctor
    surfaces parse errors separately). The expected structure is ::

        pricing:
          deepseek-v4-flash:
            input_per_1m: 3.0
            output_per_1m: 9.0
            currency: CNY
            cache_hit_input_per_1m: 0.1

    Results are mtime-cached (see ``_PRICING_CACHE``); callers get a copy.
    """
    global _PRICING_CACHE
    path = get_pricing_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _PRICING_CACHE is not None and _PRICING_CACHE[0] == key:
        return dict(_PRICING_CACHE[1])
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
    _PRICING_CACHE = (key, out)
    return dict(out)


# mtime-keyed cache — this loader sits on the per-iteration cost path.
_PRICING_CACHE: tuple[tuple[str, int, int], dict[str, Pricing]] | None = None


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
