"""API request/response schemas for the web backend.

Kept separate from ``modelbridge.models`` (the on-disk config layer) so the
HTTP surface can evolve independently. These are intentionally permissive
``extra="allow"`` mirrors of the config models — the config layer does the
real validation when we persist.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapabilitiesIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    tools: bool = False
    json: bool = False  # type: ignore[assignment]
    vision: bool = False
    reasoning: bool = False
    reasoning_content_back: bool = False
    cache: bool = False
    local: bool = False
    streaming: bool = False


class ModelIn(BaseModel):
    """Payload for creating/updating a model (a "channel")."""

    model_config = ConfigDict(extra="allow")

    name: str
    provider: str = "openai-compatible"
    type: str = "openai-compatible"
    base_url: str
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    level: str = "cheap"
    # Was accidentally ``ConfigDict(default_factory=...)`` — a raw dict as
    # the default (silently skipped validation, warned in /openapi.json).
    capabilities: CapabilitiesIn = Field(default_factory=CapabilitiesIn)
    extra: dict[str, Any] = {}


class ModelOut(BaseModel):
    """A model as returned to the frontend. ``api_key`` is never exposed."""

    model_config = ConfigDict(extra="allow")

    name: str
    provider: str
    type: str
    base_url: str
    model: str
    api_key_env: str | None = None
    has_api_key: bool = False
    level: str
    capabilities: dict[str, Any] = {}
    extra: dict[str, Any] = {}


class RoutingLevelsIn(BaseModel):
    tiny: str | None = None
    cheap: str | None = None
    coder: str | None = None
    agent: str | None = None
    expert: str | None = None


class ConfigOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    default_model: str | None = None
    routing_mode: str = "balanced"
    levels: RoutingLevelsIn = RoutingLevelsIn()
    profiles: dict[str, Any] = {}
    active_profile: str | None = None


class ProfileIn(BaseModel):
    default_model: str | None = None
    levels: RoutingLevelsIn = RoutingLevelsIn()


class PromptFiles(BaseModel):
    system: str = ""
    rules: str = ""


class PromptUpdate(BaseModel):
    content: str


class SkillOut(BaseModel):
    name: str
    description: str
    scope: str
    path: str
    body: str = ""


class Message(BaseModel):
    ok: bool = True
    message: str = ""
