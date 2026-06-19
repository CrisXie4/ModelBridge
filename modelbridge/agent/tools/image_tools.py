"""``view_image`` —— 让 AI 主动加载一张本地图片来"看"。

OpenAI 兼容的 ``role=tool`` 消息只能是纯文本，没法直接回图。所以工具返回
一句文本确认，并通过 :attr:`ToolResult.extra_messages` 追加一条携带
``image_url`` 块的 user 消息——下一轮模型即可看到这张图。
"""
from __future__ import annotations

from typing import Any

from ... import images
from ...schemas import ChatMessage
from ..context import AgentContext
from .base import Tool, ToolResult


class ViewImageTool(Tool):
    name = "view_image"
    description = (
        "加载一张本地图片让你能看到它的内容（仅 vision 模型可用）。"
        "参数 path 为相对/绝对图片路径（png/jpg/gif/webp/bmp）。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "图片文件路径（png/jpg/gif/webp/bmp）",
                }
            },
            "required": ["path"],
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        path = str(args.get("path") or "").strip()
        if not path:
            return self.err("view_image 需要 path 参数。")
        try:
            resolved = ctx.resolve(path)  # PathPolicy：越界 / 敏感文件会抛
        except Exception as e:  # noqa: BLE001 — 安全策略异常统一转成工具错误
            return self.err(f"路径不被允许: {e}")
        try:
            block = images.block_from_path(str(resolved))
        except images.ImageError as e:
            return self.err(str(e))
        name = resolved.name
        msg = ChatMessage(
            role="user",
            content=[
                {"type": "text", "text": f"[view_image 加载的图片: {name}]"},
                block,
            ],
        )
        return ToolResult(
            content=f"已加载图片 {name}，见下一条消息。",
            extra_messages=[msg],
        )


__all__ = ["ViewImageTool"]
