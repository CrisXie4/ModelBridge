"""UI preferences (terminal TUI styling) — read/write ``ui.json``.

Mirrors the bridge host's semantics: partial updates apply per-key with
per-key errors, `reset` restores factory defaults, and both the browser
sidebar and the terminal TUI pick up changes via mtime hot-reload.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...agent.ui_prefs import UiPrefs

router = APIRouter(prefix="/ui-prefs", tags=["ui-prefs"])


def _payload(prefs: UiPrefs) -> dict[str, Any]:
    from dataclasses import fields as dc_fields

    return {
        f.name: getattr(prefs, f.name)
        for f in dc_fields(type(prefs))
        if f.name != "path" and f.name != "_loaded_mtime"
    }


def _apply_values(prefs: UiPrefs, values: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for key, raw in values.items():
        try:
            prefs.set_key(str(key), raw)
        except KeyError:
            errors[str(key)] = "未知配置项"
        except (ValueError, TypeError) as e:
            errors[str(key)] = str(e)
    if not errors:
        prefs.save()
    return errors


class UiPrefsUpdate(BaseModel):
    """Partial update: only changed keys. Empty values + reset=true → defaults."""

    values: dict[str, Any] = Field(default_factory=dict)
    reset: bool = False


@router.get("")
def get_ui_prefs() -> dict[str, Any]:
    return {"prefs": _payload(UiPrefs.load())}


@router.put("")
def update_ui_prefs(payload: UiPrefsUpdate) -> dict[str, Any]:
    prefs = UiPrefs.load()
    if payload.reset:
        fresh = UiPrefs()
        from dataclasses import fields as dc_fields

        for f in dc_fields(type(fresh)):
            if f.name not in ("path", "_loaded_mtime"):
                setattr(prefs, f.name, getattr(fresh, f.name))
        prefs.clamp()
        try:
            prefs.save()
        except Exception as e:  # pragma: no cover — disk failure path
            return {"prefs": _payload(prefs), "saved": False, "errors": {"__save__": str(e)}}
        return {"prefs": _payload(prefs), "saved": True, "errors": {}}

    errors = _apply_values(prefs, payload.values)
    saved = not errors
    out_errors = dict(errors)
    if not out_errors:
        try:
            prefs.save()
        except Exception as e:  # pragma: no cover — disk failure path
            out_errors["__save__"] = str(e)
            saved = False
    return {"prefs": _payload(prefs), "saved": saved, "errors": out_errors}
