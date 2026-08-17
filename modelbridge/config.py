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
    atomic_write_text,
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
        default_model="deepseek-v4-flash",
        routing=RoutingConfig(
            mode="balanced",
            levels=RoutingLevels(
                tiny="local-qwen",
                cheap="deepseek-v4-flash",
                coder="qwen-coder",
                agent="minimax-agent",
                expert="kimi-k3",
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
    # Atomic write (temp + os.replace): an interrupted or concurrent write
    # can never leave a truncated/interleaved config.yaml / models.yaml —
    # the last writer wins cleanly instead of corrupting the file.
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)


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
    """Load ``config.yaml``, parsed+validated at most once per file change.

    The REPL hot path calls this several times per turn (session cache key,
    auto-compact, status bar, …), and a full YAML parse + pydantic validation
    costs ~1.5ms — so the parsed model is cached keyed by the file's
    (mtime_ns, size) and callers get a deep copy (callers legitimately
    mutate then re-save, e.g. ``cfg.default_model = ...``). Any write —
    ours via :func:`save_app_config` or an external editor — changes the
    key and the next load re-reads from disk.
    """
    path = get_config_path()
    key = _file_cache_key(path)
    cached = _CONFIG_CACHE
    if key is not None and cached is not None and cached[0] == key:
        return cached[1].model_copy(deep=True)
    if not path.exists():
        # Soft default — let callers decide whether to nag about `cnagent init`.
        return default_app_config()
    raw = _safe_load_yaml(path)
    raw = _migrate_raw_config(raw)
    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"config.yaml is invalid: {e}") from e
    _store_config_cache(path, cfg)
    # Hand out a copy — callers legitimately mutate the result before
    # re-saving, and the cached object must stay pristine.
    return cfg.model_copy(deep=True)


def save_app_config(cfg: AppConfig) -> None:
    _safe_dump_yaml(get_config_path(), cfg.model_dump(mode="json"))
    _invalidate_config_cache()


def _file_cache_key(path: Path) -> tuple[str, int, int] | None:
    """Cache key for a possibly-missing file (``None`` = doesn't exist)."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns, st.st_size)


# Parsed-file caches. ``_MODELS_CACHE``/``_CONFIG_CACHE`` hold the pydantic
# model plus its stat key; a mismatch (changed file / different MBRIDGE_HOME)
# forces a real reload. ``_MODELS_GENERATION`` bumps on every models.yaml
# load/save so downstream caches (provider instances hold resolved API keys)
# can invalidate when the registry changes.
_CONFIG_CACHE: tuple[tuple[str, int, int], AppConfig] | None = None
_MODELS_CACHE: tuple[tuple[str, int, int], ModelsFile] | None = None
_MODELS_GENERATION: int = 0


def _store_config_cache(path: Path, cfg: AppConfig) -> None:
    global _CONFIG_CACHE
    key = _file_cache_key(path)
    if key is not None:
        _CONFIG_CACHE = (key, cfg)


def _invalidate_config_cache() -> None:
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def models_generation() -> int:
    """Monotonic counter of models.yaml reloads — for downstream caches."""
    return _MODELS_GENERATION


# ---------------------------------------------------------------------------
# ModelsFile (models.yaml)
# ---------------------------------------------------------------------------

def load_models_file() -> ModelsFile:
    """Load ``models.yaml`` with the same change-detecting cache as
    :func:`load_app_config`. This is the single hottest loader in the REPL
    (``find_model`` runs multiple times per agent iteration); the cached
    deep copy is ~60× cheaper than re-parsing the 12-vendor registry."""
    global _MODELS_CACHE, _MODELS_GENERATION

    path = get_models_path()
    key = _file_cache_key(path)
    cached = _MODELS_CACHE
    if key is not None and cached is not None and cached[0] == key:
        return cached[1].model_copy(deep=True)
    if not path.exists():
        return default_models_file()
    raw = _safe_load_yaml(path)
    try:
        mf = ModelsFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"models.yaml is invalid: {e}") from e
    _migrate_models_secrets(mf, path)
    _MODELS_GENERATION += 1
    _MODELS_CACHE = (key, mf) if key is not None else None
    return mf.model_copy(deep=True) if _MODELS_CACHE else mf


def save_models_file(mf: ModelsFile) -> None:
    global _MODELS_CACHE, _MODELS_GENERATION
    _safe_dump_yaml(get_models_path(), mf.model_dump(mode="json"))
    _MODELS_CACHE = None
    _MODELS_GENERATION += 1


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
    # Validate that every model the profile references still exists — otherwise
    # activating it points default_model / routing.levels at deleted models and
    # routing later fails opaquely.
    referenced = [profile.default_model] if profile.default_model else []
    lv = profile.levels
    referenced += [
        getattr(lv, f) for f in ("tiny", "cheap", "coder", "agent", "expert")
        if getattr(lv, f, None)
    ]
    missing = [m for m in referenced if find_model(m) is None]
    if missing:
        raise ConfigError(
            f"profile '{name}' 引用了 models.yaml 中不存在的模型："
            f"{', '.join(sorted(set(missing)))}。请先 `mbridge model init` 或编辑该 profile。"
        )
    cfg.active_profile = name
    cfg.default_model = profile.default_model
    cfg.routing.levels = profile.levels.model_copy(deep=True)
    save_app_config(cfg)
    return profile
