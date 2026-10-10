# tests/test_model_catalog.py
"""Cross-table consistency for the built-in model catalog.

The catalog is spread over four parallel tables (provider_profiles /
cost.estimator / context.windows / agent.thinking) plus the CLI provider
menu. These tests catch drift when one table is updated but another is
forgotten — e.g. a new model lands in pricing but never gets a context
window, or an EOL id sneaks back into a profile.
"""

from __future__ import annotations

from modelbridge.agent.thinking import MODEL_THINKING_PROFILES, profile_for
from modelbridge.cli import _PROVIDER_DISPLAY_ORDER
from modelbridge.context.windows import (
    DEFAULT_UNKNOWN_WINDOW,
    context_window_for,
    DEFAULT_CONTEXT_WINDOWS,
)
from modelbridge.cost.estimator import DEFAULT_PRICING
from modelbridge.models import ModelEntry, ProviderType
from modelbridge.provider_profiles import PROFILES


def _entry(model_id: str, *, local: bool = False) -> ModelEntry:
    return ModelEntry(
        name="t", model=model_id, base_url="https://x",
        capabilities={"local": local},
    )


def _resolves_in_window_table(model_id: str) -> bool:
    """True if the id hits DEFAULT_CONTEXT_WINDOWS exactly or by prefix."""
    mid = model_id.lower()
    table = {k.lower(): v for k, v in DEFAULT_CONTEXT_WINDOWS.items()}
    return mid in table or any(mid.startswith(k) for k in table)


def test_no_foreign_provider_presets():
    # 只内置国产厂商 + 本地运行时；OpenAI 等外国厂商不提供预设。
    assert ProviderType.OPENAI not in PROFILES
    assert ProviderType.OPENAI not in _PROVIDER_DISPLAY_ORDER
    for prof in PROFILES.values():
        assert not any(
            host in prof.base_url
            for host in ("api.openai.com", "api.anthropic.com", "googleapis")
        )


def test_provider_menu_entries_all_have_profiles():
    # _pick_provider indexes PROFILES[pt] directly — a menu entry without a
    # profile would KeyError the interactive model-init flow.
    for pt in _PROVIDER_DISPLAY_ORDER:
        assert pt in PROFILES, f"{pt} in menu but has no profile"


def test_every_pricing_entry_resolves_a_context_window():
    for model_id in DEFAULT_PRICING:
        assert _resolves_in_window_table(model_id), (
            f"{model_id}: pricing exists but no context window entry"
        )


def test_every_profile_example_resolves_a_context_window():
    for prof in PROFILES.values():
        for model_id in prof.model_examples:
            if prof.is_local:
                # 本地模型运行时落到 DEFAULT_LOCAL_WINDOW 兜底，无需表项。
                continue
            assert _resolves_in_window_table(model_id), (
                f"{prof.provider.value} example {model_id}: no context window entry"
            )


def test_flagship_models_present_across_tables():
    # 用户点名的国产主力阵容 + 2026-08 扩充厂商旗舰：必须同时在示例、价格、
    # 上下文三张表里。
    flagships = [
        "deepseek-flash", "deepseek-v4-pro",
        "MiniMax-M3", "kimi-k3", "mimo-v2.5-pro", "hy3",
        "doubao-seed-evolving",
    ]
    for mid in flagships:
        assert mid in DEFAULT_PRICING, mid
        assert context_window_for(_entry(mid)) > DEFAULT_UNKNOWN_WINDOW, mid
    # 思考档位：推理旗舰要有 profile
    for mid in ("kimi-k3", "mimo-v2.5-pro", "deepseek-v4-pro", "deepseek-flash"):
        assert profile_for(mid) is not None, mid


def test_eol_model_ids_absent():
    # 已下线的型号不允许回到示例/价格表（保留 windows 兼容旧配置是允许的）。
    eol = [
        "deepseek-chat", "deepseek-reasoner", "deepseek-v3.1",
        "kimi-k2", "mimo-v2", "hy3-preview",
    ]
    for mid in eol:
        assert mid not in DEFAULT_PRICING, mid
        for prof in PROFILES.values():
            assert mid not in prof.model_examples, (
                f"{mid} still offered by {prof.provider.value}"
            )


def test_thinking_profiles_reference_real_model_families():
    # 每个 thinking profile 至少能命中一个同前缀的价格表型号（防拼写漂移）。
    pricing_ids = [p.lower() for p in DEFAULT_PRICING]
    for mid in MODEL_THINKING_PROFILES:
        assert any(pid.startswith(mid) or mid.startswith(pid) for pid in pricing_ids), (
            f"thinking profile {mid} matches no priced model"
        )


# --- E2: 声明式 ModelCatalog（单一数据源 + GLM-5.3-Flash 表达位） ------------

from modelbridge.model_catalog import (  # noqa: E402
    INPUT_FORMAT_OPENAI,
    MODEL_CATALOG,
    input_format_for,
    lookup_catalog,
)


def test_windows_table_is_derived_from_catalog():
    """windows 公开表 = 目录的非正则条目平铺（结构上不可能漂移）。"""
    derived = {e.pattern: e.context_window for e in MODEL_CATALOG if not e.regex}
    assert derived == DEFAULT_CONTEXT_WINDOWS


def test_every_catalog_entry_resolves_its_own_window():
    from modelbridge.context.windows import context_window_for as cwf

    for e in MODEL_CATALOG:
        assert cwf(_entry(e.pattern)) == e.context_window, e.pattern
        # 大小写不敏感：官方大写 id 也命中同一条目。
        assert cwf(_entry(e.pattern.upper())) == e.context_window, e.pattern


def test_lookup_exact_prefix_regex_order():
    assert lookup_catalog("deepseek-v4-pro") is not None
    assert lookup_catalog("deepseek-v4-pro-preview-1106") is not None  # 前缀继承
    assert lookup_catalog("完全未知的模型") is None
    assert input_format_for("kimi-k3") == INPUT_FORMAT_OPENAI  # 默认格式
    assert input_format_for("不存在的模型") == INPUT_FORMAT_OPENAI


def test_glm53_flash_entry_capabilities():
    """GLM-5.3-Flash：视觉/PDF 能力与 reasoning 档位首次有表达位。"""
    e = lookup_catalog("glm-5.3-flash")
    assert e is not None
    assert e.vision is True and e.pdf is True
    assert e.reasoning is not None
    assert e.context_window == 200_000  # glm-5.x（非 5.2/long）家族值
    from modelbridge.agent.thinking import profile_for

    prof = profile_for("glm-5.3-flash")
    assert prof is not None and prof.default_level == e.reasoning.default_level


def test_catalog_covers_all_derived_thinking_keys():
    """thinking 派生表的键与目录里带 reasoning 档位的条目一一对应。"""
    expected = {
        e.pattern for e in MODEL_CATALOG if e.reasoning is not None and not e.regex
    }
    assert expected == set(MODEL_THINKING_PROFILES)
