"""声明式模型目录（E2）—— 每模型事实的单一数据源。

此前每模型事实分散在平行硬编码表（context.windows 的窗口、agent.thinking
的 reasoning 档位、cost.estimator 的价格、provider_profiles 的厂商预设），
靠 tests/test_model_catalog.py 防 drift。现在 **context window / reasoning
档位 / 输入格式 / 模型级能力（vision·pdf）** 收敛到 :data:`MODEL_CATALOG`；
``context.windows.DEFAULT_CONTEXT_WINDOWS`` 与
``agent.thinking.MODEL_THINKING_PROFILES`` 变成目录的派生视图 —— 消费方
（含四表一致性测试）不变，漂移在结构上不可能。

匹配语义：

* ``regex=False``（默认）—— ``pattern`` 按**大小写不敏感前缀**匹配，
  与旧表的 longest-prefix 行为等价（released 变体自动继承家族事实）；
* ``regex=True`` —— ``pattern`` 按**大小写不敏感正则**匹配（search），
  供前缀表达不了的形态使用。

解析序（:func:`lookup_catalog`）：精确（大小写不敏感）→ 最长前缀 → 正则
（按声明序）。``context_window_for`` 的 extra.context_window 覆盖仍是最高
优先级，目录插在其后。

边界：``pricing``（钱）留在 :mod:`modelbridge.cost.estimator`、厂商级
onboarding 预设留在 :mod:`modelbridge.provider_profiles` —— 目录只收
**每模型**事实。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 输入格式（wire protocol）标识。E3 的 Anthropic Messages 传输复用该字段。
INPUT_FORMAT_OPENAI = "openai_compatible"
INPUT_FORMAT_ANTHROPIC = "anthropic_messages"


@dataclass(frozen=True)
class ReasoningTiers:
    """思考预算档位（level 1-10 线性插值的两端 + 默认档）。"""

    min_tokens: int
    max_tokens: int
    default_level: int = 5


@dataclass(frozen=True)
class CatalogEntry:
    """单个模型（家族）目录条目。"""

    pattern: str
    context_window: int
    reasoning: ReasoningTiers | None = None
    input_format: str = INPUT_FORMAT_OPENAI
    vision: bool = False
    pdf: bool = False
    regex: bool = False

    def matches(self, model_id: str) -> bool:
        mid = (model_id or "").lower()
        if not mid:
            return False
        if self.regex:
            return re.search(self.pattern, mid) is not None
        return mid.startswith(self.pattern.lower())


def _tiers(min_tokens: int, max_tokens: int, default_level: int) -> ReasoningTiers:
    return ReasoningTiers(min_tokens=min_tokens, max_tokens=max_tokens,
                          default_level=default_level)


#: 每模型事实表。窗口/reasoning 数值自 2026-07/09 起与旧表逐条核对迁移；
#: 旧表的大小写变体键（MiniMax-M3 等）由大小写不敏感前缀匹配天然覆盖。
MODEL_CATALOG: tuple[CatalogEntry, ...] = (
    # ---- DeepSeek (V4/V4.1 era; wire 只接受 reasoning_effort high/max) ----
    CatalogEntry("deepseek-flash",      1_000_000, _tiers(512, 8192, 4)),   # = V4.1-Flash
    CatalogEntry("deepseek-v4",         1_000_000, _tiers(512, 16384, 5)),
    CatalogEntry("deepseek-v4-pro",     1_000_000, _tiers(512, 16384, 5)),
    CatalogEntry("deepseek-v4-flash",   1_000_000, _tiers(512, 8192, 4)),   # 旧名兼容
    # ---- 腾讯混元 ----
    CatalogEntry("hy3",                 262_144),
    CatalogEntry("hy3-preview",         262_144),
    # ---- 字节豆包 / 火山方舟 (seed 家族默认深度思考) ----
    CatalogEntry("doubao-seed",         262_144, _tiers(512, 16384, 5)),
    CatalogEntry("doubao-1.5-pro-256k", 262_144),
    # ---- 百度文心 / 千帆 ----
    CatalogEntry("ernie-4.5-turbo-128k", 131_072),
    CatalogEntry("ernie-4.0-turbo-8k",    8_192),
    CatalogEntry("ernie-speed-128k",    131_072),
    # x1 推理系列：短 pattern 同时承载窗口前缀继承与 reasoning 档位。
    CatalogEntry("ernie-x1",             32_768, _tiers(512, 16384, 5)),
    # ---- 讯飞星火 (星辰 MaaS, X2 系列) ----
    CatalogEntry("x2",                  196_608),
    CatalogEntry("x2-flash",            262_144),
    CatalogEntry("x2-agent",            262_144),
    # ---- 阶跃星辰 StepFun ----
    CatalogEntry("step-3",               65_536),
    CatalogEntry("step-2-16k",           16_384),
    CatalogEntry("step-2-mini",          32_768),
    # ---- 商汤日日新 (V5 家族前缀) ----
    CatalogEntry("sensenova-v5",        131_072),
    # ---- Qwen / DashScope 百炼 ----
    CatalogEntry("qwen3.8-max",        1_000_000),
    CatalogEntry("qwen3.7-max",        1_000_000, _tiers(512, 32768, 6)),
    CatalogEntry("qwen3.7-plus",       1_000_000),
    CatalogEntry("qwen-plus",          1_000_000),
    CatalogEntry("qwen-plus-latest",   1_000_000),
    CatalogEntry("qwen3-plus",         1_000_000),
    CatalogEntry("qwen3.6-plus",       1_000_000, _tiers(512, 32768, 6)),
    CatalogEntry("qwen-max",             262_144),
    CatalogEntry("qwen-max-latest",      262_144, _tiers(512, 16384, 5)),
    CatalogEntry("qwen3-max",            262_144, _tiers(512, 32768, 6)),
    CatalogEntry("qwen3.7-max-preview", 1_000_000, _tiers(512, 32768, 6)),
    CatalogEntry("qwen-turbo",         1_000_000),
    CatalogEntry("qwen-turbo-latest",  1_000_000),
    CatalogEntry("qwen3-coder-plus",   1_000_000, _tiers(512, 16384, 5)),
    CatalogEntry("qwen3-coder-flash",  1_000_000, _tiers(512, 8192, 4)),
    CatalogEntry("qwen-long",         10_000_000),
    # ---- Kimi / Moonshot (K2.x + K3) ----
    CatalogEntry("kimi-k3",            1_000_000, _tiers(512, 32768, 7)),
    CatalogEntry("kimi-k2.5",            262_144),
    CatalogEntry("kimi-k2.6",            262_144),
    CatalogEntry("kimi-k2-thinking",     262_144, _tiers(512, 32768, 7)),
    CatalogEntry("kimi-k2-thinking-turbo", 262_144, _tiers(512, 32768, 9)),
    CatalogEntry("kimi-k2.7-code",       262_144, _tiers(512, 32768, 7)),
    # ---- MiMo / 小米 (V2.5 era) ----
    CatalogEntry("mimo-v2.5",          1_000_000, _tiers(512, 16384, 5)),
    CatalogEntry("mimo-v2.5-pro",      1_000_000, _tiers(512, 16384, 6)),
    CatalogEntry("mimo-v2-omni",         262_144),   # V2 系列已下线，兼容旧配置
    CatalogEntry("mimo-v2-tts",          131_072),   # V2 系列已下线，兼容旧配置
    # ---- GLM / 智谱 (5.x era) ----
    CatalogEntry("glm-5.2",            1_000_000, _tiers(512, 16384, 5)),
    CatalogEntry("glm-5.3-flash",        200_000, _tiers(512, 16384, 5),
                 vision=True, pdf=True),               # Coding Plan Flash；
    #   窗口沿用 glm-5.x（非 5.2/long）家族值 200K，以官方文档为准。
    CatalogEntry("glm-5.1",              200_000),
    CatalogEntry("glm-5",                200_000),
    CatalogEntry("glm-4.7",              200_000),
    CatalogEntry("glm-4.6",              200_000),
    CatalogEntry("glm-4-long",         1_000_000),
    # ---- MiniMax (M3 era；官方 id 首字母大写含点，前缀匹配天然覆盖) ----
    CatalogEntry("minimax-m3",         1_000_000),
    CatalogEntry("minimax-m2.7",         204_800),
    CatalogEntry("minimax-m2.5",         204_800),
)

#: 正则条目（当前无；保留表达位 —— 前缀表达不了的形态用它）。
_MODEL_CATALOG_REGEX: tuple[CatalogEntry, ...] = tuple(
    e for e in MODEL_CATALOG if e.regex
)


def lookup_catalog(model_id: str) -> CatalogEntry | None:
    """精确（大小写不敏感）→ 最长前缀 → 正则（声明序）；无命中返回 None。"""
    mid = (model_id or "").lower()
    if not mid:
        return None
    for e in MODEL_CATALOG:
        if not e.regex and e.pattern.lower() == mid:
            return e
    prefix = [
        e for e in MODEL_CATALOG
        if not e.regex and mid.startswith(e.pattern.lower())
    ]
    if prefix:
        return max(prefix, key=lambda e: len(e.pattern))
    for e in _MODEL_CATALOG_REGEX:
        if e.matches(mid):
            return e
    return None


def input_format_for(model_id: str) -> str:
    """该模型的输入格式（wire protocol）；未命中走 OpenAI-compatible 默认。"""
    entry = lookup_catalog(model_id)
    return entry.input_format if entry is not None else INPUT_FORMAT_OPENAI


__all__ = [
    "INPUT_FORMAT_ANTHROPIC",
    "INPUT_FORMAT_OPENAI",
    "CatalogEntry",
    "MODEL_CATALOG",
    "ReasoningTiers",
    "input_format_for",
    "lookup_catalog",
]
