"""Mutating browser tools — click / fill / navigate.

Each one calls :meth:`AgentContext.confirm` *before* relaying to the page, so
the host turns it into an approval card in the side panel (mirroring how
``write_file`` / ``str_replace`` confirm in the CLI). A denied / timed-out
approval returns an error to the model instead of acting.
"""

from __future__ import annotations

from typing import Any

from ..context import AgentContext
from .base import ToolResult
from .browser_tools import _BrowserTool


class _WriteBrowserTool(_BrowserTool):
    def _approval(self, args: dict[str, Any]) -> tuple[str, str]:
        """Return ``(summary, detail)`` shown on the approval card."""
        return self.name, ""

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        if ctx.browser_bridge is None:
            return self.err("浏览器工具仅在侧边栏 (LocalBridge) 环境可用。")
        summary, detail = self._approval(args)
        # Shared group: choosing ALWAYS on any browser write tool auto-approves
        # all of them this session (web automation chains click/fill/navigate).
        if not ctx.confirm(tool=self.name, summary=summary, detail=detail,
                           group="browser_write", pattern_key="browser_write", auto=True):
            return self.err("用户拒绝了该操作。")
        return self._relay(args, ctx)


class ClickTool(_WriteBrowserTool):
    name = "click"
    description = (
        "点击当前网页上匹配 CSS selector 的第一个元素。需要用户确认。"
        "若点击触发页面跳转，会自动等新页面加载完成后才返回，之后可直接 read_page。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "要点击元素的 CSS 选择器。"}
            },
            "required": ["selector"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str]:
        sel = args.get("selector", "")
        return "点击元素", f"selector: {sel}"


class FillTool(_WriteBrowserTool):
    name = "fill"
    description = (
        "把文本填入匹配 CSS selector 的输入框 / textarea (会触发 input 事件)。需要用户确认。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "目标输入框的 CSS 选择器。"},
                "value": {"type": "string", "description": "要填入的文本。"},
            },
            "required": ["selector", "value"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str]:
        sel = args.get("selector", "")
        val = str(args.get("value", ""))
        preview = val if len(val) <= 80 else val[:80] + "…"
        return "填写输入框", f"selector: {sel}\nvalue: {preview}"


class NavigateTool(_WriteBrowserTool):
    name = "navigate"
    description = (
        "让当前标签页跳转到指定 URL，并自动等待新页面加载完成后才返回；"
        "之后可直接用 read_page 读取新页面。需要用户确认。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要打开的完整 URL (含 http/https)。"}
            },
            "required": ["url"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str]:
        return "跳转页面", f"url: {args.get('url', '')}"


__all__ = ["ClickTool", "FillTool", "NavigateTool"]
