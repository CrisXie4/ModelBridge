"""Tests for the thinking subsystem (modelbridge/agent/thinking.py + /think command).

Covers:
  * profile lookup (exact + longest-prefix + unknown → None)
  * level → budget interpolation (clamped 1-10)
  * named-level parsing (low / med / high / xhigh / min / max)
  * numeric-level parsing (1-10; out-of-range → None)
  * per-profile default_level sanity
  * ThinkingProfile validation (min > 0, max >= min, default in 1-10)
"""

from __future__ import annotations

import pytest

from modelbridge.agent.thinking import (
    MODEL_THINKING_PROFILES,
    NAMED_LEVELS,
    ThinkingProfile,
    budget_for,
    parse_level,
    profile_for,
)


# ---------------------------------------------------------------------------
# profile_for / budget_for
# ---------------------------------------------------------------------------

class TestProfileLookup:
    def test_exact_match_kimi_thinking(self):
        p = profile_for("kimi-k2-thinking")
        assert p is not None
        assert p.min_tokens == 512
        assert p.max_tokens == 32768
        assert p.default_level == 7

    def test_exact_match_qwen_coder(self):
        p = profile_for("qwen3-coder-plus")
        assert p is not None
        assert p.default_level == 5

    def test_longest_prefix_fallback(self):
        # An unreleased variant of qwen3-coder-plus inherits the family profile.
        p = profile_for("qwen3-coder-plus-2026-preview")
        assert p is not None
        assert p.min_tokens == 512

    def test_longest_prefix_picks_longest(self):
        # qwen3-coder-flash has its own entry; qwen3-coder-plus-... should
        # match the more specific "qwen3-coder-plus" prefix if there's a
        # tie on length, but the longest match overall wins.
        p_specific = profile_for("qwen3-coder-flash")
        p_fallback = profile_for("qwen3-coder-flash-extra")
        assert p_specific is not None
        assert p_fallback is not None
        # both should be flash-family (specific match wins, prefix same length)
        assert p_specific.min_tokens == p_fallback.min_tokens

    def test_unknown_model_returns_none(self):
        assert profile_for("gpt-4o") is None
        assert profile_for("claude-opus-4") is None
        assert profile_for("unknown-model-xyz") is None

    def test_empty_string_returns_none(self):
        assert profile_for("") is None

    def test_case_insensitive(self):
        p = profile_for("KIMI-K2-THINKING")
        assert p is not None
        assert p.default_level == 7

    def test_minimax_m3_no_profile(self):
        # MiniMax M3 doesn't have a thinking profile (not a reasoning model
        # in our table) — UI will warn and budget is skipped.
        assert profile_for("minimax-m3") is None


class TestBudgetFor:
    def test_known_model_known_level(self):
        # kimi-k2-thinking: min=512, max=32768, default=7
        # level 1 → 512, level 10 → 32768
        # level 5: 512 + (32768 - 512) * 4 // 9 = 512 + 32256*4//9 = 512 + 14336 = 14848
        assert budget_for("kimi-k2-thinking", 1) == 512
        assert budget_for("kimi-k2-thinking", 10) == 32768
        assert budget_for("kimi-k2-thinking", 5) == 14848

    def test_interpolation_step(self):
        # Each level should increase by approximately (max - min) / 9.
        b1 = budget_for("kimi-k2-thinking", 1)
        b2 = budget_for("kimi-k2-thinking", 2)
        b10 = budget_for("kimi-k2-thinking", 10)
        step = b2 - b1
        # step ≈ (max - min) / 9 = 32256/9 ≈ 3584
        assert 3500 < step < 3700
        # And b10 - b1 should equal exactly 9 * step (modulo integer rounding)
        assert (b10 - b1) == 9 * step

    def test_unknown_model_returns_none(self):
        assert budget_for("nonexistent-model", 5) is None

    def test_invalid_level_returns_none(self):
        # Level 0 / 11 are out of 1-10 range
        assert budget_for("kimi-k2-thinking", 0) == 512  # clamped to 1
        assert budget_for("kimi-k2-thinking", 11) == 32768  # clamped to 10
        # Non-int returns None
        assert budget_for("kimi-k2-thinking", "abc") is None  # type: ignore[arg-type]
        assert budget_for("kimi-k2-thinking", None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_level
# ---------------------------------------------------------------------------

class TestParseLevel:
    @pytest.mark.parametrize("name,expected", [
        ("low",    3),
        ("med",    6),
        ("high",   9),
        ("xhigh", 10),
        ("min",    1),
        ("max",   10),
    ])
    def test_named_levels(self, name, expected):
        assert parse_level(name) == expected

    def test_named_case_insensitive(self):
        assert parse_level("HIGH") == 9
        assert parse_level("XHigh") == 10

    @pytest.mark.parametrize("n", [1, 2, 5, 7, 9, 10])
    def test_numeric_levels(self, n):
        assert parse_level(str(n)) == n

    @pytest.mark.parametrize("bad", ["0", "11", "15", "-1", "abc", "", "  ", "foo"])
    def test_invalid_levels(self, bad):
        assert parse_level(bad) is None

    def test_whitespace_stripped(self):
        assert parse_level("  high  ") == 9
        assert parse_level("  7  ") == 7


# ---------------------------------------------------------------------------
# ThinkingProfile validation + interpolation
# ---------------------------------------------------------------------------

class TestThinkingProfile:
    def test_min_must_be_positive(self):
        with pytest.raises(ValueError, match="min_tokens"):
            ThinkingProfile(min_tokens=0, max_tokens=100)

    def test_max_must_be_ge_min(self):
        with pytest.raises(ValueError, match="max_tokens"):
            ThinkingProfile(min_tokens=1000, max_tokens=500)

    def test_default_level_must_be_in_range(self):
        with pytest.raises(ValueError, match="default_level"):
            ThinkingProfile(min_tokens=100, max_tokens=1000, default_level=0)
        with pytest.raises(ValueError, match="default_level"):
            ThinkingProfile(min_tokens=100, max_tokens=1000, default_level=11)

    def test_interpolation_endpoints(self):
        p = ThinkingProfile(min_tokens=1000, max_tokens=10000, default_level=5)
        assert p.budget_for_level(1) == 1000
        assert p.budget_for_level(10) == 10000

    def test_interpolation_midpoint(self):
        # Linear: min + (max - min) * (level - 1) // 9
        p = ThinkingProfile(min_tokens=1000, max_tokens=10000, default_level=5)
        # level 5: 1000 + 9000 * 4 // 9 = 1000 + 4000 = 5000
        assert p.budget_for_level(5) == 5000

    def test_interpolation_clamps_out_of_range(self):
        p = ThinkingProfile(min_tokens=100, max_tokens=1000)
        assert p.budget_for_level(0) == 100   # clamped to 1
        assert p.budget_for_level(11) == 1000  # clamped to 10
        assert p.budget_for_level(-5) == 100  # clamped to 1

    def test_default_level_5_typical(self):
        # Sanity check: every entry in MODEL_THINKING_PROFILES has a sane
        # default_level in 1-10 (catches drift from manual edits).
        for model_id, profile in MODEL_THINKING_PROFILES.items():
            assert 1 <= profile.default_level <= 10, f"{model_id}: bad default_level"
            assert profile.min_tokens > 0, f"{model_id}: bad min_tokens"
            assert profile.max_tokens >= profile.min_tokens, f"{model_id}: max < min"


# ---------------------------------------------------------------------------
# NAMED_LEVELS table sanity
# ---------------------------------------------------------------------------

class TestNamedLevelsTable:
    def test_no_duplicate_values_unless_intentional(self):
        # We allow "max" and "xhigh" to both map to 10, but no other dupes.
        from collections import Counter
        counts = Counter(NAMED_LEVELS.values())
        dupes = {v: c for v, c in counts.items() if c > 1}
        assert dupes == {10: 2}, f"Unexpected duplicates in NAMED_LEVELS: {dupes}"

    def test_all_values_in_1_to_10(self):
        for name, level in NAMED_LEVELS.items():
            assert 1 <= level <= 10, f"{name}={level} out of range"
