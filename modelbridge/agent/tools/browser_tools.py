"""Remote browser tools — executed in the page, not on disk.

Unlike :mod:`file_tools`, these don't touch the local filesystem. Each one
forwards its arguments over :class:`~modelbridge.agent.context.BrowserBridge`
to the side-panel extension, which runs the action against the active tab's DOM
and replies with the result.

Read tools (``read_page`` / ``get_selection`` / ``query_dom`` / ``extract``)
need no confirmation. Write tools (``click`` / ``fill`` / ``navigate``, added
in Stage 3) call :meth:`AgentContext.confirm` first — the host turns that into
an approval card in the side panel.

The actual DOM logic lives in ``extension/sidepanel.js`` (the page-tool
dispatcher); these classes only declare the schema the model sees and relay
the call.
"""

from __future__ import annotations

from typing import Any

from ..context import AgentContext
from .base import Tool, ToolResult
from .registry import ToolRegistry


class _BrowserTool(Tool):
    """Base: forward ``args`` to the page via the browser bridge."""

    def _relay(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        bridge = ctx.browser_bridge
        if bridge is None:
            return self.err("浏览器工具仅在侧边栏 (LocalBridge) 环境可用。")
        res = bridge.call(self.name, args)
        content = res.get("content", "")
        if res.get("ok"):
            return self.ok(content)
        return self.err(content or f"{self.name} 执行失败")

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        return self._relay(args, ctx)


# ---------------------------------------------------------------------------
# Read tools (no confirmation)
# ---------------------------------------------------------------------------

class ReadPageTool(_BrowserTool):
    name = "read_page"
    description = (
        "读取当前网页的标题、URL 和可见正文文本 (会截断到约 max_chars 字符)。"
        "用它来理解或总结用户正在看的页面。"
        "页面正在加载时会自动等待加载完成后再读取，无需让用户手动等待或刷新。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_chars": {
                    "type": "integer",
                    "description": "正文最大字符数 (默认 8000，上限 40000)。",
                }
            },
            "additionalProperties": False,
        }


class GetSelectionTool(_BrowserTool):
    name = "get_selection"
    description = "获取用户在当前网页上选中的文本。没有选中时返回空。"

    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}


class QueryDomTool(_BrowserTool):
    name = "query_dom"
    description = (
        "用 CSS selector 查询当前网页的元素，返回每个匹配元素的标签、文本摘要和关键属性。"
        "用它来定位要点击或填写的元素 (再配合 click / fill)。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器，例如 'button.submit'。"},
                "limit": {"type": "integer", "description": "最多返回多少个元素 (默认 20)。"},
            },
            "required": ["selector"],
            "additionalProperties": False,
        }


class ExtractTool(_BrowserTool):
    name = "extract"
    description = (
        "提取匹配 selector 的元素的文本 (默认) 或某个属性值 (传 attr)。"
        "返回所有匹配项，每行一个。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器。"},
                "attr": {
                    "type": "string",
                    "description": "要提取的属性名 (如 'href')；省略则取文本。",
                },
            },
            "required": ["selector"],
            "additionalProperties": False,
        }


def build_browser_registry(*, include_write: bool = False) -> ToolRegistry:
    """Registry of browser tools. Read tools always; write tools opt-in (Stage 3)."""
    reg = ToolRegistry()
    reg.register(ReadPageTool())
    reg.register(GetSelectionTool())
    reg.register(QueryDomTool())
    reg.register(ExtractTool())
    if include_write:
        from .browser_write_tools import ClickTool, FillTool, NavigateTool

        reg.register(ClickTool())
        reg.register(FillTool())
        reg.register(NavigateTool())
    return reg


__all__ = [
    "ReadPageTool",
    "GetSelectionTool",
    "QueryDomTool",
    "ExtractTool",
    "build_browser_registry",
]
