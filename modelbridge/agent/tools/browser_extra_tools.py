"""Browser tools beyond click/fill/navigate — keystrokes.

``PressKeyTool`` sends a keystroke to the page (Enter submits the form) and
shares the browser_write confirm group. Cookie reading lives in
``browser_cookie_tool``; page-JS execution stayed in ``computer_control_tools``
(``InjectJsTool``) and is re-registered by ``build_browser_registry`` so the
side panel gets it too.
"""

from __future__ import annotations

from typing import Any

from .browser_write_tools import _WriteBrowserTool


class PressKeyTool(_WriteBrowserTool):
    name = "press_key"
    description = (
        "在当前网页上按一个键：Enter 提交搜索/表单、Escape 关弹窗、Tab 切焦点、方向键滚动列表等。"
        "默认模拟 DOM 键盘事件 (Enter 会顺带提交所在表单)；严格反检测站点可传 trusted=true "
        "走 CDP 真实输入事件 (isTrusted=true，浏览器顶部会出现调试提示条)。需要用户确认。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "按键名，如 'Enter' / 'Escape' / 'Tab' / 'a' / 'ArrowDown'。",
                },
                "selector": {
                    "type": "string",
                    "description": "目标元素 CSS 选择器；省略则发给当前焦点元素。",
                },
                "trusted": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=CDP 真实输入事件(isTrusted=true，过严格反检测，会弹调试提示)；"
                    "false=模拟DOM键盘事件(默认，无提示)。",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要按这个键 (一句话，给用户审批看)。",
                },
            },
            "required": ["key", "reason"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str, str]:
        sel = args.get("selector") or "(当前焦点元素)"
        mode = "CDP 真实输入" if args.get("trusted") else "模拟按键"
        detail = f"key: {args.get('key', '')}\ntarget: {sel}\n方式: {mode}"
        return "按下按键", detail, str(args.get("reason", ""))


__all__ = ["PressKeyTool"]
