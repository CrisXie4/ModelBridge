"""Mutating browser tools — click / fill / navigate.

Each one calls :meth:`AgentContext.confirm` *before* relaying to the page, so
the host turns it into an approval card in the side panel (mirroring how
``write_file`` / ``str_replace`` confirm in the CLI). A denied / timed-out
approval returns an error to the model instead of acting.

Every write tool requires a ``reason`` parameter — the model's one-sentence
justification for the action. It is shown on the approval card and fed to the
LLM safety judge so context-aware auto-approval can distinguish "clearing
cache" from "deleting an account".
"""

from __future__ import annotations

from typing import Any

from ..context import AgentContext
from .base import ToolResult
from .browser_tools import _BrowserTool


class _WriteBrowserTool(_BrowserTool):
    def _approval(self, args: dict[str, Any]) -> tuple[str, str, str]:
        """Return ``(summary, detail, reason)`` for the approval card.

        ``reason`` is the model's justification from ``args["reason"]`` — empty
        string if the model omitted it (the schema marks it required, but a
        defensive fallback keeps the card readable on malformed calls).
        """
        return self.name, "", str(args.get("reason", ""))

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        if ctx.browser_bridge is None:
            return self.err("浏览器工具仅在侧边栏 (LocalBridge) 环境可用。")
        summary, detail, reason = self._approval(args)
        # Shared group: choosing ALWAYS on any browser write tool auto-approves
        # all of them this session (web automation chains click/fill/navigate).
        if not ctx.confirm(tool=self.name, summary=summary, detail=detail,
                           reason=reason,
                           group="browser_write", pattern_key="browser_write", auto=True):
            return self.err("用户拒绝了该操作。")
        return self._relay(args, ctx)


class ClickTool(_WriteBrowserTool):
    name = "click"
    description = (
        "点击当前网页上匹配 CSS selector 的第一个元素。需要用户确认。"
        "默认走模拟真人点击 (完整鼠标事件序列 + 随机时序)，能过多数反 bot 检测。"
        "若传 trusted=true 则走 CDP 真实输入事件 (isTrusted=true，过严格反检测，"
        "但浏览器顶部会出现调试警告条)。"
        "若点击触发页面跳转，会自动等新页面加载完成后才返回，之后可直接 read_page。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "要点击元素的 CSS 选择器。"},
                "reason": {
                    "type": "string",
                    "description": "为什么要点这个 (一句话，给用户审批看，例如「清缓存以释放空间」)。",
                },
                "trusted": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=CDP 真实输入事件(isTrusted=true，过严格反检测，会弹调试警告)；"
                    "false=软模拟DOM事件序列(默认，无警告)。",
                },
            },
            "required": ["selector", "reason"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str, str]:
        sel = args.get("selector", "")
        trusted = bool(args.get("trusted"))
        mode = "CDP 真实输入" if trusted else "模拟点击"
        detail = f"selector: {sel}\n方式: {mode}"
        return "点击元素", detail, str(args.get("reason", ""))


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
                "reason": {
                    "type": "string",
                    "description": "为什么要填这个 (一句话，给用户审批看)。",
                },
            },
            "required": ["selector", "value", "reason"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str, str]:
        sel = args.get("selector", "")
        val = str(args.get("value", ""))
        preview = val if len(val) <= 80 else val[:80] + "…"
        return "填写输入框", f"selector: {sel}\nvalue: {preview}", str(args.get("reason", ""))


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
                "url": {"type": "string", "description": "要打开的完整 URL (含 http/https)。"},
                "reason": {
                    "type": "string",
                    "description": "为什么要跳转 (一句话，给用户审批看)。",
                },
            },
            "required": ["url", "reason"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str, str]:
        return "跳转页面", f"url: {args.get('url', '')}", str(args.get("reason", ""))


__all__ = ["ClickTool", "FillTool", "NavigateTool"]
