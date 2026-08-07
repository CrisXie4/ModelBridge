"""System prompt + rules file editing."""

from __future__ import annotations

from fastapi import APIRouter

from ...prompt.defaults import DEFAULT_RULES_MD, DEFAULT_SYSTEM_MD
from ...prompt.rules_loader import discover_rule_files
from ..schemas import Message, PromptFiles, PromptUpdate

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _system_path():
    from ...utils import get_app_dir

    return get_app_dir() / "system.md"


def _rules_path():
    from ...utils import get_app_dir

    return get_app_dir() / "rules.md"


@router.get("", response_model=PromptFiles)
def get_prompts() -> PromptFiles:
    sp = _system_path()
    rp = _rules_path()
    system = sp.read_text(encoding="utf-8") if sp.exists() else DEFAULT_SYSTEM_MD
    rules = rp.read_text(encoding="utf-8") if rp.exists() else DEFAULT_RULES_MD
    return PromptFiles(system=system, rules=rules)


@router.put("/system", response_model=Message)
def update_system(payload: PromptUpdate) -> Message:
    _system_path().write_text(payload.content, encoding="utf-8")
    return Message(message="system.md 已更新")


@router.put("/rules", response_model=Message)
def update_rules(payload: PromptUpdate) -> Message:
    _rules_path().write_text(payload.content, encoding="utf-8")
    return Message(message="rules.md 已更新")


@router.get("/sources")
def prompt_sources() -> dict:
    """List all rule files that contribute to the prompt prefix."""
    files = discover_rule_files(None)
    return {
        "sources": [
            {"path": str(f.path), "scope": f.scope, "exists": f.path.exists()}
            for f in files
        ]
    }
