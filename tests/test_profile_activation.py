"""Tests for activate_profile model-existence validation.

BUG: activate_profile() was mirroring profile.default_model and profile.levels
into top-level config WITHOUT checking those models exist in models.yaml.
Activating a profile that references a deleted/renamed model left the config in
a broken state where routing later failed with an opaque "找不到模型" error.

FIX: validate every referenced model exists (via find_model) BEFORE setting
cfg.active_profile, raising ConfigError with a clear message if any are missing.
"""

from __future__ import annotations

import pytest

from modelbridge.config import (
    ConfigError,
    activate_profile,
    load_app_config,
    upsert_model,
    upsert_profile,
)
from modelbridge.models import (
    ModelEntry,
    ModelLevel,
    ProfileEntry,
    RoutingLevels,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point ModelBridge at an isolated MBRIDGE_HOME and seed it via init."""
    monkeypatch.setenv("MBRIDGE_HOME", str(tmp_path))
    # Seed by importing and calling init logic directly (creates config.yaml +
    # models.yaml with default content), mirroring the CLI smoke test pattern.
    from typer.testing import CliRunner
    from modelbridge.cli import app
    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _make_model(name: str, level: ModelLevel = ModelLevel.CHEAP) -> ModelEntry:
    """Build a minimal valid ModelEntry suitable for upsert_model."""
    return ModelEntry(
        name=name,
        base_url="https://api.example.com/v1",
        model=name,
        level=level,
    )


# ---------------------------------------------------------------------------
# Test 1 (RED before fix): profile referencing a non-existent model raises ConfigError
# ---------------------------------------------------------------------------

def test_activate_profile_with_missing_model_raises_config_error():
    """A profile that references a model not in models.yaml must raise ConfigError.

    PRE-FIX: this test FAILS because activate_profile() succeeds silently.
    POST-FIX: this test PASSES because validation raises ConfigError.
    """
    # Seed a real model so the profile can have one valid level and one bad one.
    upsert_model(_make_model("real-model", ModelLevel.CHEAP))

    # Profile references "ghost-model" which was never added to models.yaml.
    bad_profile = ProfileEntry(
        default_model="ghost-model",
        levels=RoutingLevels(
            cheap="real-model",
            expert="also-missing",
        ),
    )
    upsert_profile("bad-profile", bad_profile)

    with pytest.raises(ConfigError) as exc_info:
        activate_profile("bad-profile")

    msg = str(exc_info.value)
    # Both missing models should appear in the error message.
    assert "ghost-model" in msg or "also-missing" in msg
    # The profile should NOT have been activated.
    cfg = load_app_config()
    assert cfg.active_profile != "bad-profile"


# ---------------------------------------------------------------------------
# Test 2 (GREEN): profile whose models all exist activates successfully
# ---------------------------------------------------------------------------

def test_activate_profile_with_all_models_present_succeeds():
    """A profile whose every referenced model exists in models.yaml must succeed."""
    upsert_model(_make_model("tiny-model", ModelLevel.TINY))
    upsert_model(_make_model("cheap-model", ModelLevel.CHEAP))
    upsert_model(_make_model("expert-model", ModelLevel.EXPERT))

    good_profile = ProfileEntry(
        default_model="cheap-model",
        levels=RoutingLevels(
            tiny="tiny-model",
            cheap="cheap-model",
            expert="expert-model",
        ),
    )
    upsert_profile("good-profile", good_profile)

    returned = activate_profile("good-profile")

    # Return value should be the profile itself.
    assert returned.default_model == "cheap-model"

    # Top-level config must reflect the activated profile.
    cfg = load_app_config()
    assert cfg.active_profile == "good-profile"
    assert cfg.default_model == "cheap-model"
    assert cfg.routing.levels.tiny == "tiny-model"
    assert cfg.routing.levels.expert == "expert-model"
