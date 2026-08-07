"""web_search: 调用 ModelBridge 服务端 ``/v1/search`` 的联网搜索工具。

仅在网络只读,不读写本地文件、不操作浏览器,因此不需要 ``ctx.confirm``。
凭据从 ``~/.modelbridge/search.json`` 读取(由 ``mbridge search login`` 写入)。
``execute`` 抛出的异常会被 ``ToolRegistry.dispatch`` 兜底转成 error ToolResult,
但这里主动捕获 :class:`SearchError` 以给出可操作的提示(配额耗尽 / 未登录等)。
"""

from __future__ import annotations

from typing import Any

from ...search.client import SearchError, do_search
from ...search.creds import load_credentials
from ..context import AgentContext
from .base import Tool, ToolResult


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "联网搜索引擎,返回最相关的若干条网页结果(标题 / 摘要 / 链接)。"
        "适用于需要最新信息、事实核查、实时数据或网络资料的场景。"
        "需要先 `mbridge search login` 登录。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词。"},
                "top_k": {
                    "type": "integer",
                    "description": "返回条数(默认 5,上限 20)。",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:  # noqa: ARG002
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return self.err("缺少必填参数 query")
        try:
            top_k = int(args.get("top_k") or 5)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(20, top_k))

        creds = load_credentials()
        if not creds or not creds.get("api_key") or not creds.get("crypto_key"):
            return self.err(
                "未登录联网搜索",
                hint="运行 `mbridge search login` 完成授权后再试。",
            )

        try:
            result = do_search(
                creds["endpoint"],
                api_key=creds["api_key"],
                crypto_key=creds["crypto_key"],
                query=query.strip(),
                count=top_k,
            )
        except SearchError as exc:
            hint = {
                401: "apikey 无效或已过期,请重新 `mbridge search login`。",
                402: "搜索配额已用尽,请升级套餐或等待配额重置。",
                403: "请求被风控拒绝,请稍后再试或联系管理员。",
                429: "请求过于频繁,请稍后再试。",
            }.get(exc.status_code)
            return self.err(f"搜索失败: {exc}", hint=hint)

        results = result.get("results") or []
        if not results:
            return self.ok(
                f"(未找到与「{query}」相关的结果)",
                structured={"query": query, "count": 0},
            )

        lines: list[str] = []
        for idx, item in enumerate(results, 1):
            title = str(item.get("title") or "(无标题)")
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            line = f"{idx}. [{title}]({url})" if url else f"{idx}. {title}"
            if snippet:
                line += f"\n   {snippet}"
            lines.append(line)
        body = "\n".join(lines)

        cost = result.get("cost")
        remaining = result.get("remaining_quota")
        if remaining is not None:
            body += f"\n\n_(本次消耗 {cost} 次配额,剩余 {remaining})_"

        return self.ok(
            body,
            structured={
                "query": query,
                "count": len(results),
                "cost": cost,
                "remaining": remaining,
            },
        )
