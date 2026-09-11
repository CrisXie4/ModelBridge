"""Chat routes — create conversations, submit turns, long-poll events, resolve approvals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..chat_service import get_chat_service

router = APIRouter(prefix="/chats", tags=["chats"])


class ChatIn(BaseModel):
    title: str = ""


class MessageIn(BaseModel):
    text: str
    model: str | None = None


class ApprovalIn(BaseModel):
    decision: str = Field(pattern="^(y|n)$")
    # Optional free text sent back to the model in the tool's rejection
    # error ("用户反馈：不要动这个文件，改用 X") — empty = plain rejection.
    feedback: str = Field(default="", max_length=2000)


@router.get("")
def list_chats() -> dict[str, Any]:
    return {"chats": get_chat_service().list_chats()}


@router.post("")
def create_chat(payload: ChatIn) -> dict[str, Any]:
    return get_chat_service().create(payload.title)


@router.delete("/{chat_id}")
def delete_chat(chat_id: str) -> dict[str, bool]:
    if not get_chat_service().delete(chat_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.get("/{chat_id}/events")
def chat_events(chat_id: str, after: int = 0) -> dict[str, Any]:
    snap = get_chat_service().snapshot(chat_id, after)
    if snap is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return snap


@router.post("/{chat_id}/send")
def send_message(chat_id: str, payload: MessageIn) -> dict[str, Any]:
    svc = get_chat_service()
    default_model: str | None = None
    try:
        from ...config import load_app_config

        default_model = load_app_config().default_model
    except Exception:
        pass
    out = svc.submit(chat_id, payload.text, payload.model, default_model)
    if not out.get("ok"):
        status = 409 if (out.get("error") or "").startswith("busy") else 404
        raise HTTPException(status_code=status, detail=out.get("error", "提交失败"))
    return {"ok": True}


@router.post("/approvals/{approval_id}")
def resolve_approval(approval_id: str, payload: ApprovalIn) -> dict[str, bool]:
    # 注意前缀：挂载后路径为 /api/chats/approvals/{id}，见 server.py 说明。
    ok = get_chat_service().resolve_approval(approval_id, payload.decision, payload.feedback)
    if not ok:
        raise HTTPException(status_code=404, detail="审批不存在或已处理")
    return {"ok": True}
