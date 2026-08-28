"""MCP server management — persistent enable/disable per server.

Reads the ``mcp:`` block from ``config.yaml`` (same source the REPL uses at
startup). Toggling here is the *persistent* plane; a running terminal REPL
keeps its own runtime switch (``/mcp on|off <id>``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...mcp.config import load_mcp_settings, set_server_enabled

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("")
def list_servers() -> dict[str, Any]:
    settings = load_mcp_settings()
    servers = [
        {
            "server_id": s.server_id,
            "transport": s.transport.value if hasattr(s.transport, "value") else str(s.transport),
            "command": s.command,
            "url": s.url,
            "enabled": s.enabled,
            "tool_policy": getattr(s.tool_policy, "value", str(s.tool_policy)),
        }
        for s in settings.servers
    ]
    return {
        "enabled": settings.enabled,
        "servers": servers,
        "note": "开关写入 config.yaml（持久）。已运行会话的临时启停用 /mcp on|off <id>。",
    }


class EnabledIn(BaseModel):
    enabled: bool


@router.put("/{server_id}/enabled")
def toggle_server(server_id: str, payload: EnabledIn) -> dict[str, bool]:
    ok = set_server_enabled(server_id, payload.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' 不存在")
    return {"ok": True, "enabled": payload.enabled}
