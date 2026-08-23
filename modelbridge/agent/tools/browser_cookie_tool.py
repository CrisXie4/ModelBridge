"""Cookie 读取工具 — 读取类但带独立确认卡。

Cookie 值往往就是登录态，所以它的确认策略 (见基类的 ``_confirm_group``
/ ``_confirm_auto``) 是：不共享 browser_write 的"本会话总是"记忆，也不
参与 AI 安全判定自动放行 —— 每次调用都要用户亲眼确认。
"""

from __future__ import annotations

from typing import Any

from .browser_write_tools import _WriteBrowserTool


class GetCookiesTool(_WriteBrowserTool):
    name = "get_cookies"
    # 独立确认：None = 不进共享组 (always 不互相传染)；False = 不走 AI 判定。
    _confirm_group = None
    _confirm_auto = False
    description = (
        "查看当前网站的 Cookie (含 httpOnly)。Cookie 里往往带有登录态，"
        "所以每次调用都要用户在确认卡上放行，不会自动通过。"
        "传 name 只看名字里含有该词的条目；include_domain=true 返回整站 (含子域) 的全部条目。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "只看名字里含有该词的 Cookie；省略返回全部。",
                },
                "include_domain": {
                    "type": "boolean",
                    "default": False,
                    "description": "true=按站点域名取全部 Cookie (含子域)；默认只取当前 URL 的。",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要看 Cookie (一句话，给用户审批看)。",
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        }

    def _approval(self, args: dict[str, Any]) -> tuple[str, str, str]:
        return "读取 Cookie", "当前网站的 Cookie 明细将展示给模型。", str(args.get("reason", ""))


__all__ = ["GetCookiesTool"]
