"""Pydantic schemas for ModelBridge configuration.

These cover both ``models.yaml`` (the model registry) and ``config.yaml``
(global defaults, routing, security, cache).

The design intentionally keeps a ``provider`` discriminator field separate
from ``type`` so that future provider-specific adapters (DeepSeek reasoning,
Qwen ``enable_thinking``, MiMo ``reasoning_content`` re-injection, etc.)
can be plugged in without touching the storage layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProviderType(str, Enum):
    """Logical provider hint.

    All providers currently dispatch through the ``openai-compatible``
    transport, but this enum preserves the original choice so future
    adapters can implement provider-specific behaviour.
    """

    OPENAI_COMPATIBLE = "openai-compatible"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    KIMI = "kimi"
    MIMO = "mimo"
    GLM = "glm"
    MINIMAX = "minimax"
    OLLAMA = "ollama"
    VLLM = "vllm"
    LMSTUDIO = "lmstudio"
    OPENAI = "openai"
    CUSTOM = "custom"


class TransportType(str, Enum):
    """Underlying transport / wire protocol."""

    OPENAI_COMPATIBLE = "openai-compatible"


class ModelLevel(str, Enum):
    TINY = "tiny"
    CHEAP = "cheap"
    CODER = "coder"
    AGENT = "agent"
    EXPERT = "expert"


# ---------------------------------------------------------------------------
# Model registry (models.yaml)
# ---------------------------------------------------------------------------

class Capabilities(BaseModel):
    """Per-model capability flags.

    ``reasoning_content_back`` is critical for MiMo and some Kimi/DeepSeek
    thinking models: when re-sending an assistant turn that contained tool
    calls, the original ``reasoning_content`` MUST be preserved or the API
    will return 400.
    """

    tools: bool = False
    # ``json`` intentionally shadows the deprecated pydantic ``BaseModel.json()``
    # method — it's a capability flag exposed in configs as ``capabilities.json``.
    json: bool = False  # type: ignore[assignment]
    vision: bool = False
    reasoning: bool = False
    reasoning_content_back: bool = False
    cache: bool = False
    local: bool = False
    streaming: bool = False  # added in v0.2 — defaults False for old configs


class ModelEntry(BaseModel):
    """A single registered model."""

    # Tolerate fields written by future versions without crashing
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="Unique display name used in the CLI.")
    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE
    type: TransportType = TransportType.OPENAI_COMPATIBLE
    base_url: str
    api_key_env: str | None = Field(
        default=None,
        description="Environment variable name to read the API key from.",
    )
    # Stored encrypted-at-rest: a ``keyring:<name>`` reference or ``enc:<token>``
    # ciphertext (see :mod:`modelbridge.secrets`). Legacy plaintext values are
    # still accepted and auto-migrated on load. Prefer ``api_key_env`` entirely.
    api_key: str = ""
    model: str = Field(..., description="The provider-side model id.")
    level: ModelLevel = ModelLevel.CHEAP
    capabilities: Capabilities = Field(default_factory=Capabilities)
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Provider-specific extras (temperature, max_tokens, "
            "enable_thinking, thinking_budget, etc.). Forwarded into the "
            "request body where it makes sense."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model name must not be empty")
        return v

    @field_validator("provider", mode="before")
    @classmethod
    def _coerce_provider(cls, v: Any) -> Any:
        # Unknown providers (e.g. user-written "zhipu") fall back to
        # openai-compatible. v0.2 doctor will surface a warning.
        if isinstance(v, str):
            try:
                return ProviderType(v)
            except ValueError:
                return ProviderType.OPENAI_COMPATIBLE
        return v

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v: Any) -> Any:
        if v is None or v == "":
            return ModelLevel.CHEAP
        if isinstance(v, str):
            try:
                return ModelLevel(v)
            except ValueError:
                return ModelLevel.CHEAP
        return v

    @field_validator("capabilities", mode="before")
    @classmethod
    def _coerce_capabilities(cls, v: Any) -> Any:
        # Old v0.1 configs may have ``null`` or missing capabilities.
        if v is None:
            return Capabilities()
        return v



class ModelsFile(BaseModel):
    """Schema for ``~/.cnagent/models.yaml``."""

    models: list[ModelEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Global config (config.yaml)
# ---------------------------------------------------------------------------

class RoutingLevels(BaseModel):
    tiny: str | None = None
    cheap: str | None = None
    coder: str | None = None
    agent: str | None = None
    expert: str | None = None


class RoutingFallbackConfig(BaseModel):
    enabled: bool = True
    max_upgrade_steps: int = 2


class RoutingRulesConfig(BaseModel):
    prefer_local_for_tiny: bool = True
    prefer_cache_supported: bool = True
    prefer_low_cost: bool = True


class RoutingConfig(BaseModel):
    mode: str = "balanced"
    levels: RoutingLevels = Field(default_factory=RoutingLevels)
    fallback: RoutingFallbackConfig = Field(default_factory=RoutingFallbackConfig)
    rules: RoutingRulesConfig = Field(default_factory=RoutingRulesConfig)

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return "balanced"
        m = v.lower().strip()
        if m in ("economy", "balanced", "powerful"):
            return m
        return "balanced"


class ProfileEntry(BaseModel):
    """A named bundle of model selections.

    Activating a profile copies ``default_model`` and ``levels`` into
    ``AppConfig.default_model`` / ``AppConfig.routing.levels`` so the rest
    of the system (router, REPL, cost, doctor) keeps reading top-level
    fields unchanged.
    """

    model_config = ConfigDict(extra="ignore")

    default_model: str | None = None
    levels: RoutingLevels = Field(default_factory=RoutingLevels)


class SecurityConfig(BaseModel):
    allowed_project_dirs: list[str] = Field(default_factory=list)
    block_sensitive_files: list[str] = Field(
        default_factory=lambda: [
            ".env",
            "id_rsa",
            "id_ed25519",
            ".ssh",
            "config.json",
            "secrets.yaml",
        ]
    )


class ExecutorConfig(BaseModel):
    """User-extensible allowlist for ``mbridge run`` / future ``loop``.

    The built-in deny list (``rm``, ``shutdown`` …) is always enforced and
    cannot be overridden from config. ``allowed_commands`` is unioned with
    the hardcoded defaults (``pytest``, ``python``, ``npm`` …) so users can
    add project-specific tooling like ``tsc`` or ``jest``.
    """

    allowed_commands: list[str] = Field(default_factory=list)


class CacheConfig(BaseModel):
    enabled: bool = True
    strategy: str = "stable-prefix"


class PromptConfig(BaseModel):
    """Where the user's system prompt + global rules live.

    All paths are optional — when unset we fall back to
    ``~/.modelbridge/system.md`` and ``~/.modelbridge/rules.md``
    respectively.
    """

    model_config = ConfigDict(extra="ignore")

    system_file: str | None = None
    user_rules_file: str | None = None
    use_project_rules: bool = True
    use_claude_md: bool = True
    use_agent_md: bool = True
    max_rules_chars: int = 20000
    inject_position: str = "before_user_request"


# Current config.yaml schema version. Bump when a breaking structural change
# lands, and add a matching ``v→v+1`` entry to ``config._MIGRATIONS``.
CURRENT_SCHEMA_VERSION = 1


class AppConfig(BaseModel):
    """Schema for ``~/.modelbridge/config.yaml``."""

    schema_version: int = CURRENT_SCHEMA_VERSION
    default_model: str | None = None
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    profiles: dict[str, ProfileEntry] = Field(default_factory=dict)
    active_profile: str | None = None
