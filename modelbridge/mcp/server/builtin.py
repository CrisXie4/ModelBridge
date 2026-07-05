"""The built-in ModelBridge MCP server: chat / list_models / route as tools.

This is what an MCP host (Claude Desktop, Cursor, another ModelBridge…) sees
when it connects to ``mbridge mcp serve``. The host never touches our API
keys — calls go through the same provider registry as ``mbridge chat``.
"""

from __future__ import annotations

import json
from typing import Any

from ... import __version__
from .server import MCPServer, ServerTool


def _tool_chat(args: dict[str, Any]) -> str:
    from ...client import chat_once

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    model = args.get("model")
    system = args.get("system")
    entry, resp = chat_once(
        prompt,
        model_name=str(model) if model else None,
        system=str(system) if system else None,
        timeout=120.0,
        verbose_label="mcp_server_chat",
    )
    return resp.content or ""


def _tool_list_models(args: dict[str, Any]) -> str:
    from ...config import load_models_file

    mf = load_models_file()
    rows = [
        {
            "name": m.name,
            "model": m.model,
            "provider": str(m.provider.value if hasattr(m.provider, "value") else m.provider),
            "level": str(m.level.value if hasattr(m.level, "value") else m.level),
        }
        for m in mf.models
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _tool_route(args: dict[str, Any]) -> str:
    from ...router import route

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    r = route(prompt, use_llm=True)
    return json.dumps(
        {
            "task_type": r.profile.task_type,
            "level": r.level.value,
            "chosen_model": r.chosen_model,
            "reasons": r.profile.reasons,
        },
        ensure_ascii=False,
        indent=2,
    )


def build_modelbridge_server() -> MCPServer:
    server = MCPServer(
        name="modelbridge",
        version=str(__version__),
        instructions=(
            "ModelBridge 网关：chat 用配置好的国产模型补全；"
            "list_models 列出可用模型；route 为任务推荐模型档位。"
        ),
    )
    server.register(ServerTool(
        name="chat",
        description="用 ModelBridge 配置的模型生成一次补全（国产模型优先）。",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "用户提示词"},
                "model": {"type": "string", "description": "模型名（缺省用 default_model）"},
                "system": {"type": "string", "description": "可选 system 提示"},
            },
            "required": ["prompt"],
        },
        fn=_tool_chat,
    ))
    server.register(ServerTool(
        name="list_models",
        description="列出 ModelBridge 已配置的模型（JSON：name/model/provider/level）。",
        input_schema={"type": "object", "properties": {}},
        fn=_tool_list_models,
    ))
    server.register(ServerTool(
        name="route",
        description="对一个任务做路由分类，返回推荐档位与模型（JSON）。",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "要分类的任务描述"},
            },
            "required": ["prompt"],
        },
        fn=_tool_route,
    ))
    return server


__all__ = ["build_modelbridge_server"]
