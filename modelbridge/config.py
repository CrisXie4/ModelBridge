"""Read / write configuration YAML files under ``~/.cnagent/``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import (
    CURRENT_SCHEMA_VERSION,
    AppConfig,
    CacheConfig,
    ExecutorConfig,
    ModelEntry,
    ModelsFile,
    ProfileEntry,
    RoutingConfig,
    RoutingLevels,
    SecurityConfig,
)
from .secrets import is_protected, protect
from .utils import (
    get_app_dir,
    get_config_path,
    get_logs_dir,
    get_models_path,
)


class ConfigError(Exception):
    """Raised for any config load / save / validation error."""


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def default_app_config() -> AppConfig:
    """Return the seed ``config.yaml`` written by ``cnagent init``.

    The level→model bindings are illustrative; they refer to model names the
    user may not have configured yet. That is fine — they are just hints.
    """
    return AppConfig(
        default_model="deepseek-chat",
        routing=RoutingConfig(
            mode="balanced",
            levels=RoutingLevels(
                tiny="local-qwen",
                cheap="deepseek-chat",
                coder="qwen-coder",
                agent="minimax-agent",
                expert="kimi-k2",
            ),
        ),
        security=SecurityConfig(),
        cache=CacheConfig(),
        executor=ExecutorConfig(),
    )


def default_models_file() -> ModelsFile:
    return ModelsFile(models=[])


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_app_dir(*, force: bool = False) -> dict[str, bool]:
    """Create ``~/.modelbridge/`` and seed config / models YAML + prompt files.

    Returns a dict reporting which files were created vs. skipped.
    Existing user files are preserved unless ``force=True``.
    """
    app_dir = get_app_dir()
    app_dir.mkdir(parents=True, exist_ok=True)

    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)

    config_path = get_config_path()
    models_path = get_models_path()

    result: dict[str, bool] = {}

    if not config_path.exists() or force:
        save_app_config(default_app_config())
        result["config.yaml"] = True
    else:
        result["config.yaml"] = False

    if not models_path.exists() or force:
        save_models_file(default_models_file())
        result["models.yaml"] = True
    else:
        result["models.yaml"] = False

    # Seed system.md / rules.md — local import avoids a config↔prompt cycle.
    from .prompt.defaults import DEFAULT_RULES_MD, DEFAULT_SYSTEM_MD

    system_path = app_dir / "system.md"
    rules_path = app_dir / "rules.md"
    if not system_path.exists() or force:
        system_path.write_text(DEFAULT_SYSTEM_MD, encoding="utf-8")
        result["system.md"] = True
    else:
        result["system.md"] = False
    if not rules_path.exists() or force:
        rules_path.write_text(DEFAULT_RULES_MD, encoding="utf-8")
        result["rules.md"] = True
    else:
        result["rules.md"] = False

    return result


# ---------------------------------------------------------------------------
# Generic YAML helpers
# ---------------------------------------------------------------------------

def _safe_load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"failed to parse YAML at {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"expected mapping at top of {path}, got {type(data).__name__}")
    return data


def _safe_dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


# ---------------------------------------------------------------------------
# AppConfig (config.yaml)
# ---------------------------------------------------------------------------

# Ordered schema migrations. ``_MIGRATIONS[v]`` upgrades a ``v`` → ``v+1`` raw
# dict. No migrations exist yet (v1 is current); this is scaffolding so a future
# breaking change (field rename / restructure) has a versioned home instead of
# silently breaking old configs.
#
# Example for when schema_version bumps to 2:
#     def _migrate_v1_to_v2(raw: dict) -> dict:
#         raw["new_field"] = raw.pop("old_field", default)
#         return raw
#     _MIGRATIONS = {1: _migrate_v1_to_v2}
_MIGRATIONS: dict[int, Any] = {}


def _migrate_raw_config(raw: Any) -> Any:
    """Run ordered schema migrations on the raw dict before validation.

    Legacy configs without ``schema_version`` are treated as v1. Returns the
    (possibly upgraded) dict; unknown / future versions are left untouched so
    validation can surface a clear error rather than corrupting data.
    """
    if not isinstance(raw, dict):
        return raw
    try:
        version = int(raw.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        version = 1
    while version < CURRENT_SCHEMA_VERSION:
        migrate = _MIGRATIONS.get(version)
        if migrate is None:
            break  # no path forward — let validation handle whatever's there
        raw = migrate(raw)
        version += 1
    return raw


def load_app_config() -> AppConfig:
    path = get_config_path()
    if not path.exists():
        # Soft default — let callers decide whether to nag about `cnagent init`.
        return default_app_config()
    raw = _safe_load_yaml(path)
    raw = _migrate_raw_config(raw)
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"config.yaml is invalid: {e}") from e


def save_app_config(cfg: AppConfig) -> None:
    _safe_dump_yaml(get_config_path(), cfg.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# ModelsFile (models.yaml)
# ---------------------------------------------------------------------------

def load_models_file() -> ModelsFile:
    path = get_models_path()
    if not path.exists():
        return default_models_file()
    raw = _safe_load_yaml(path)
    try:
        mf = ModelsFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"models.yaml is invalid: {e}") from e
    _migrate_models_secrets(mf, path)
    return mf


def save_models_file(mf: ModelsFile) -> None:
    _safe_dump_yaml(get_models_path(), mf.model_dump(mode="json"))


def _migrate_models_secrets(mf: ModelsFile, path: Path) -> None:
    """One-time: move any plaintext ``api_key`` into secure storage on load.

    Only rewrites ``models.yaml`` when at least one key was actually protected
    (so it's a no-op once migrated, and on systems without keyring/cryptography
    it doesn't thrash — ``protect`` returns the value unchanged there).
    """
    migrated = 0
    for m in mf.models:
        if m.api_key and not is_protected(m.api_key):
            token = protect(m.name, m.api_key)
            if token != m.api_key:
                m.api_key = token
                migrated += 1
    if migrated:
        try:
            save_models_file(mf)
        except OSError:
            return  # leave plaintext in place; retried next load
        print(
            f"[modelbridge] 已将 {migrated} 个明文 API key 迁移到安全存储 "
            f"(keyring / 加密)，并从 {path.name} 移除明文。",
            file=sys.stderr,
        )


def find_model(name: str) -> ModelEntry | None:
    mf = load_models_file()
    for m in mf.models:
        if m.name == name:
            return m
    return None


def upsert_model(entry: ModelEntry) -> bool:
    """Insert or replace a model entry. Returns True if replaced."""
    # Encrypt-at-rest: never persist a plaintext api_key to models.yaml.
    if entry.api_key and not is_protected(entry.api_key):
        entry.api_key = protect(entry.name, entry.api_key)
    mf = load_models_file()
    replaced = False
    for i, m in enumerate(mf.models):
        if m.name == entry.name:
            mf.models[i] = entry
            replaced = True
            break
    if not replaced:
        mf.models.append(entry)
    save_models_file(mf)
    return replaced


def remove_model(name: str) -> bool:
    mf = load_models_file()
    before = len(mf.models)
    mf.models = [m for m in mf.models if m.name != name]
    if len(mf.models) == before:
        return False
    save_models_file(mf)
    return True


# ---------------------------------------------------------------------------
# Profiles (named bundles of default_model + routing.levels)
# ---------------------------------------------------------------------------

def list_profiles() -> tuple[dict[str, ProfileEntry], str | None]:
    """Return (profiles, active_name)."""
    cfg = load_app_config()
    return dict(cfg.profiles), cfg.active_profile


def find_profile(name: str) -> ProfileEntry | None:
    cfg = load_app_config()
    return cfg.profiles.get(name)


def upsert_profile(name: str, profile: ProfileEntry) -> bool:
    """Insert or replace a profile. Returns True if replaced."""
    cfg = load_app_config()
    replaced = name in cfg.profiles
    cfg.profiles[name] = profile
    save_app_config(cfg)
    return replaced


def remove_profile(name: str) -> bool:
    """Delete a profile. Refuses to remove the currently-active one."""
    cfg = load_app_config()
    if name not in cfg.profiles:
        return False
    if cfg.active_profile == name:
        raise ConfigError(
            f"profile '{name}' 是当前激活配置，请先 `mbridge profile use <其他>` 再删除。"
        )
    del cfg.profiles[name]
    save_app_config(cfg)
    return True


def activate_profile(name: str) -> ProfileEntry:
    """Mark a profile active and mirror its contents into top-level
    ``default_model`` / ``routing.levels`` so router/REPL pick it up
    without any other code changes."""
    cfg = load_app_config()
    profile = cfg.profiles.get(name)
    if profile is None:
        raise ConfigError(f"profile '{name}' 不存在。可用：{', '.join(cfg.profiles) or '(无)'}")
    cfg.active_profile = name
    cfg.default_model = profile.default_model
    cfg.routing.levels = profile.levels.model_copy(deep=True)
    save_app_config(cfg)
    return profile
