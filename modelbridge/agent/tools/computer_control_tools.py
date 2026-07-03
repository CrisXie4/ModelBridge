"""System-level computer control: mouse, keyboard, screen, and inject_js.

These tools let the AI interact with the OS and browser outside the chat context.
pyautogui is used for cross-platform mouse/keyboard; browser injection is handled
separately by the inject_js tool that forwards to the side-panel extension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..context import AgentContext
from .base import Tool, ToolResult

if TYPE_CHECKING:
    from .registry import ToolRegistry

try:
    import pyautogui

    _PYAUTOGUI_AVAILABLE = True
except Exception:
    _PYAUTOGUI_AVAILABLE = False


class _ComputerTool(Tool):
    """Base for system-level computer control tools."""

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        if not _PYAUTOGUI_AVAILABLE:
            return self.err(
                "pyautogui 未安装。运行: pip install pyautogui\n"
                "（注意：Windows 上需以管理员权限运行以控制鼠标/键盘）"
            )
        return self._do(args, ctx)

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mouse & Keyboard
# ---------------------------------------------------------------------------


class MouseMoveTool(_ComputerTool):
    name = "mouse_move"
    description = (
        "移动鼠标指针到屏幕指定坐标 (x, y)。\n"
        "注意：坐标基于屏幕分辨率 (0,0 为左上角)。可配合 screenshot 查看当前位置。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "目标 X 坐标（像素）"},
                "y": {"type": "integer", "description": "目标 Y 坐标（像素）"},
                "duration": {"type": "number", "description": "移动耗时（秒），默认 0.5"},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        x = int(args.get("x", 0))
        y = int(args.get("y", 0))
        duration = float(args.get("duration", 0.5))
        pyautogui.moveTo(x, y, duration=duration)
        return self.ok(f"鼠标已移动到 ({x}, {y})")


class MouseClickTool(_ComputerTool):
    name = "mouse_click"
    description = (
        "在当前鼠标位置（或指定坐标）点击鼠标键。\n"
        "可选: button='left'（默认）| 'right' | 'middle'，clicks=点击次数。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X 坐标（省略则使用当前位置）"},
                "y": {"type": "integer", "description": "Y 坐标（省略则使用当前位置）"},
                "button": {"type": "string", "description": "鼠标键: left / right / middle（默认 left）"},
                "clicks": {"type": "integer", "description": "点击次数（默认 1）"},
            },
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        x = args.get("x")
        y = args.get("y")
        button = str(args.get("button", "left"))
        clicks = int(args.get("clicks", 1))
        if x is not None and y is not None:
            pyautogui.click(x=int(x), y=int(y), button=button, clicks=clicks)
        else:
            pyautogui.click(button=button, clicks=clicks)
        loc = f"({x}, {y})" if x is not None and y is not None else "当前位置"
        return self.ok(f"已在 {loc} {button} 点击 {clicks} 次")


class MouseDragTool(_ComputerTool):
    name = "mouse_drag"
    description = (
        "拖拽鼠标从起点 (x1, y1) 到终点 (x2, y2)。\n"
        "按住鼠标键移动，常用于滑块、选择等操作。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "起点 X"},
                "y1": {"type": "integer", "description": "起点 Y"},
                "x2": {"type": "integer", "description": "终点 X"},
                "y2": {"type": "integer", "description": "终点 Y"},
                "duration": {"type": "number", "description": "拖拽耗时（秒），默认 0.5"},
            },
            "required": ["x1", "y1", "x2", "y2"],
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        x1 = int(args["x1"])
        y1 = int(args["y1"])
        x2 = int(args["x2"])
        y2 = int(args["y2"])
        duration = float(args.get("duration", 0.5))
        pyautogui.moveTo(x1, y1)
        pyautogui.dragTo(x2, y2, duration=duration)
        return self.ok(f"已从 ({x1}, {y1}) 拖拽到 ({x2}, {y2})")


class ScrollTool(_ComputerTool):
    name = "scroll"
    description = "在当前位置滚动鼠标滚轮。amount 为滚动量（正数=向上，负数=向下）。"

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "滚动量（正=上，负=下），默认 -3"},
                "x": {"type": "integer", "description": "滚动位置 X（省略则当前鼠标）"},
                "y": {"type": "integer", "description": "滚动位置 Y（省略则当前鼠标）"},
            },
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        amount = int(args.get("amount", -3))
        x = args.get("x")
        y = args.get("y")
        if x is not None and y is not None:
            pyautogui.scroll(amount, x=int(x), y=int(y))
        else:
            pyautogui.scroll(amount)
        return self.ok(f"已滚动 {'向上' if amount > 0 else '向下'} {abs(amount)} 步")


class TypewriteTool(_ComputerTool):
    name = "typewrite"
    description = "在当前焦点位置键入文本（相当于逐字符按键盘）。interval 为每字符间隔秒数。"

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文本"},
                "interval": {"type": "number", "description": "每字符间隔（秒），默认 0"},
            },
            "required": ["text"],
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        text = str(args.get("text", ""))
        interval = float(args.get("interval", 0))
        pyautogui.typewrite(text, interval=interval)
        preview = text[:50] + ("…" if len(text) > 50 else "")
        return self.ok(f"已输入: {preview}")


class HotkeyTool(_ComputerTool):
    name = "hotkey"
    description = "按下一个或多个组合键（如 ctrl+c、alt+tab）。按住的顺序为 key1 按下→key2 按下→key2 松开→key1 松开。"

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "description": "组合键列表，如 ['ctrl', 'c'] 或 ['ctrl', 'v']",
                    "items": {"type": "string"},
                }
            },
            "required": ["keys"],
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        keys = args.get("keys", [])
        if not keys:
            return self.err("keys 不能为空")
        pyautogui.hotkey(*[str(k) for k in keys])
        return self.ok(f"已按下组合键: {' + '.join(str(k) for k in keys)}")


class ScreenshotTool(_ComputerTool):
    name = "screenshot"
    description = "截取当前屏幕截图并返回图片路径。可选 region=(x,y,w,h) 只截取区域。"

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "保存路径（默认临时文件）"},
                "region": {
                    "type": "array",
                    "description": "裁剪区域 [x, y, width, height]",
                    "items": {"type": "number"},
                },
            },
            "additionalProperties": False,
        }

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        import tempfile

        path = args.get("path")
        region = args.get("region")
        if region and len(region) == 4:
            img = pyautogui.screenshot(region=[int(r) for r in region])
        else:
            img = pyautogui.screenshot()
        if not path:
            fd, path = tempfile.mkstemp(suffix=".png")
            import os

            os.close(fd)
        img.save(path)
        return self.ok(f"截图已保存: {path}")


class GetMousePositionTool(_ComputerTool):
    name = "get_mouse_position"
    description = "获取鼠标当前屏幕坐标。"

    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    def _do(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        x, y = pyautogui.position()
        return self.ok(f"鼠标位置: ({x}, {y})")


# ---------------------------------------------------------------------------
# Browser inject_js — relayed through browser_bridge, auto-cleaned
# ---------------------------------------------------------------------------


class InjectJsTool(Tool):
    """Inject JavaScript into the active browser tab via the side-panel bridge.

    After execution the injected script is automatically removed using
    ``chrome.scripting.removeScript`` to keep the page clean.
    """

    name = "inject_js"
    description = (
        "在当前浏览器标签页注入并执行一段 JavaScript 代码，执行完成后自动移除。\n"
        "用于一次性脚本（如自动化操作、DOM 修改、数据提取等）。\n"
        "⚠ 会弹出确认框。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 JavaScript 代码（函数体，返回值会作为结果）",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        code: str = args.get("code", "")
        if not code.strip():
            return self.err("code 不能为空")

        bridge = ctx.browser_bridge
        if bridge is None:
            return self.err("浏览器工具仅在侧边栏环境可用。")

        if not ctx.confirm(
            tool=self.name,
            summary="注入 JavaScript",
            detail=f"执行后将自动移除:\n{code[:200]}{'…' if len(code) > 200 else ''}",
            group="browser_write",
            pattern_key="inject_js",
            auto=True,
        ):
            return self.err("用户拒绝了 JS 注入。")

        res = bridge.call(self.name, {"code": code})
        content = res.get("content", "")
        if res.get("ok"):
            return self.ok(content)
        return self.err(content or "JS 注入执行失败")


def build_computer_registry() -> "ToolRegistry":
    """Return registry with all computer control tools."""
    from .registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(MouseMoveTool())
    reg.register(MouseClickTool())
    reg.register(MouseDragTool())
    reg.register(ScrollTool())
    reg.register(TypewriteTool())
    reg.register(HotkeyTool())
    reg.register(ScreenshotTool())
    reg.register(GetMousePositionTool())
    reg.register(InjectJsTool())
    return reg


__all__ = [
    "MouseMoveTool",
    "MouseClickTool",
    "MouseDragTool",
    "ScrollTool",
    "TypewriteTool",
    "HotkeyTool",
    "ScreenshotTool",
    "GetMousePositionTool",
    "InjectJsTool",
    "build_computer_registry",
]
