"""wire_search: 已登录时注册 ``WebSearchTool`` 并向 system prompt 追加能力说明。

统一在 REPL / 微信网关 / 浏览器侧边栏三处调用,避免"某通道能搜、另一通道
不能搜"的能力不一致。未登录时什么都不做(既不注册工具、也不改提示词),
模型也就不会去调用一个必然失败的 web_search。

**功能开关**: :data:`SEARCH_ENABLED` 是付费联网搜索的总开关。当前代码尚未
完工（搜索后端 / 套餐体系还在开发），默认关闭——所有调用方在关闭时直接返回
"未注册 / 原样提示词",工具不会被注册、提示词不会被改。功能完工后把
``SEARCH_ENABLED`` 改为 ``True`` 即可全量启用,无需改任何调用方。
"""

from __future__ import annotations

from ..agent.tools import ToolRegistry

#: 付费联网搜索总开关。当前为开发中状态,默认关闭以避免用户看到一个能用
#: 但实际不可用的功能。三处调用方(REPL / 微信网关 / 浏览器侧边栏)统一读
#: 这个开关;关闭时 maybe_register_web_search 返回 False、wire_search 原样
#: 返回提示词。功能完工后改为 True 即全量启用。
SEARCH_ENABLED: bool = False


def is_logged_in() -> bool:
    from .creds import load_credentials

    return bool(load_credentials())


def maybe_register_web_search(registry: ToolRegistry) -> bool:
    """已登录且功能开关打开时注册 ``WebSearchTool``,返回是否注册。

    开关关闭(:data:`SEARCH_ENABLED` 为 False)时立即返回 False,不读凭据、
    不注册工具——这是暂停状态的预期行为。
    """
    if not SEARCH_ENABLED:
        return False
    if not is_logged_in():
        return False
    from ..agent.tools.web_search_tool import WebSearchTool

    registry.register(WebSearchTool())
    return True


def wire_search(registry: ToolRegistry, system_prompt: str) -> str:
    """已登录且开关打开则注册工具 + 追加提示词;否则原样返回。

    与 :func:`modelbridge.skills.wiring.wire_skills` 同构:条件成立才动 prompt,
    且追加的文本是稳定常量(不含 cwd / 时间戳),以维持 system prompt 的稳定
    prefix(供 PromptBuilder 的 prefix-hash / 缓存统计使用)。
    """
    if not maybe_register_web_search(registry):
        return system_prompt
    return system_prompt + (
        "\n\n## 联网搜索\n"
        "你已登录 ModelBridge 联网搜索服务。当问题需要最新信息、事实核查、"
        "实时数据或网络资料时,调用 `web_search(query, top_k?)` 工具联网搜索,"
        "再基于返回的标题 / 摘要 / 链接作答,并在回答中引用来源链接。"
    )


__all__ = ["SEARCH_ENABLED", "is_logged_in", "maybe_register_web_search", "wire_search"]
