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
        "deepseek-v4-flash", "deepseek-v4-pro",
        "MiniMax-M3", "kimi-k3", "mimo-v2.5-pro", "hy3",
        "doubao-seed-evolving",
    ]
    for mid in flagships:
        assert mid in DEFAULT_PRICING, mid
        assert context_window_for(_entry(mid)) > DEFAULT_UNKNOWN_WINDOW, mid
    # 思考档位：推理旗舰要有 profile
    for mid in ("kimi-k3", "mimo-v2.5-pro", "deepseek-v4-pro"):
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
