"""Routing config + profiles — mirrors ``mbridge config`` / ``mbridge config profile``."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...config import (
    activate_profile,
    load_app_config,
    remove_profile,
    save_app_config,
    upsert_profile,
)
from ...models import ProfileEntry, RoutingLevels
from ..schemas import ConfigOut, Message, ProfileIn, RoutingLevelsIn

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=ConfigOut)
def get_config() -> ConfigOut:
    cfg = load_app_config()
    return ConfigOut(
        default_model=cfg.default_model,
        routing_mode=cfg.routing.mode,
        levels=RoutingLevelsIn(**cfg.routing.levels.model_dump()),
        profiles={k: v.model_dump() for k, v in cfg.profiles.items()},
        active_profile=cfg.active_profile,
    )


@router.put("", response_model=ConfigOut)
def update_config(payload: ConfigOut) -> ConfigOut:
    cfg = load_app_config()
    cfg.default_model = payload.default_model
    cfg.routing.mode = payload.routing_mode
    cfg.routing.levels = RoutingLevels(**payload.levels.model_dump())
    save_app_config(cfg)
    return get_config()


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

@router.get("/profiles")
def list_profiles() -> dict:
    cfg = load_app_config()
    return {
        "profiles": {k: v.model_dump() for k, v in cfg.profiles.items()},
        "active": cfg.active_profile,
    }


@router.post("/profiles/{name}", response_model=Message)
def create_profile(name: str, payload: ProfileIn) -> Message:
    profile = ProfileEntry(
        default_model=payload.default_model,
        levels=RoutingLevels(**payload.levels.model_dump()),
    )
    upsert_profile(name, profile)
    return Message(message=f"已保存 profile '{name}'")


@router.post("/profiles/{name}/activate", response_model=Message)
def activate(name: str) -> Message:
    try:
        activate_profile(name)
    except Exception as e:  # noqa: BLE001 — surface config errors as HTTP 400
        raise HTTPException(status_code=400, detail=str(e))
    return Message(message=f"已激活 profile '{name}'")


@router.delete("/profiles/{name}", response_model=Message)
def delete_profile(name: str) -> Message:
    try:
        if not remove_profile(name):
            raise HTTPException(status_code=404, detail=f"profile '{name}' 不存在")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — surface config errors as HTTP 400
        raise HTTPException(status_code=400, detail=str(e))
    return Message(message=f"已删除 profile '{name}'")
