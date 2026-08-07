"""Skills listing — built-in, global, and project scopes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...skills.discovery import discover_skills, find_skill
from ..schemas import SkillOut

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("")
def list_skills() -> dict:
    skills = discover_skills()
    # Show full body only on detail endpoint; list gives the index.
    return {
        "skills": [
            SkillOut(
                name=s.name,
                description=s.description,
                scope=s.scope,
                path=str(s.path),
            ).model_dump()
            for s in skills
        ]
    }


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
    )
