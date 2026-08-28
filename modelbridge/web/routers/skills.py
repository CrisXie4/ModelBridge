"""Skills listing + per-skill enable/disable (persisted at skills.json)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...skills.discovery import discover_skills, find_skill
from ...skills.prefs import load_disabled, set_enabled
from ..schemas import SkillOut

router = APIRouter(prefix="/skills", tags=["skills"])


class EnabledIn(BaseModel):
    enabled: bool


@router.get("")
def list_skills() -> dict:
    skills = discover_skills()
    disabled = load_disabled()
    # Show full body only on detail endpoint; list gives the index.
    return {
        "skills": [
            SkillOut(
                name=s.name,
                description=s.description,
                scope=s.scope,
                path=str(s.path),
                enabled=s.name not in disabled,
            ).model_dump()
            for s in skills
        ]
    }


@router.put("/{name}/enabled")
def toggle_skill(name: str, payload: EnabledIn) -> dict:
    if find_skill(name) is None:
        raise HTTPException(status_code=404, detail=f"skill '{name}' 不存在")
    set_enabled(name, enabled=payload.enabled)
    return {"name": name, "enabled": payload.enabled}


@router.get("/{name}")
def show_skill(name: str) -> SkillOut:
    skill = find_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill '{name}' 不存在")
    return SkillOut(
        name=skill.name,
        description=skill.description,
        scope=skill.scope,
        path=str(skill.path),
        body=skill.body,
        enabled=skill.name not in load_disabled(),
    )
