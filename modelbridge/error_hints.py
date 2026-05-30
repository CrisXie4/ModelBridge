"""Chinese-first error diagnosis for provider failures.

Provider adapters call :func:`hint_for_http_error` / :func:`hint_for_exception`
to attach an actionable suggestion to a :class:`ProviderError`. The CLI
displays the hint in the red error panel.

Keep this module dependency-free: it must be safe to call from any layer.
"""

from __future__ import annotations

from typing import Any

import httpx


# Generic per-status hints. Provider adapters may *prepend* provider-specific
# wisdom, but should always fall back to these for common cases.
_STATUS_HINTS: dict[int, str] = {
    400: (
        "字段不兼容或参数不被支持。常见原因：模型名错误、模型不支持 tools/json/thinking 等参数、"
        "thinking 模型的历史 assistant 消息未保留 reasoning_content。"
    ),
    401: "API Key 错误或未设置。请检查 api_key / api_key_env，或确认环境变量已经 export。",
    402: "账户余额不足，或该模型需要付费开通。",
    403: "API Key 有效但没有调用该模型的权限。请到控制台开通模型 / 申请权限。",
    404: (
        "endpoint 或模型 ID 不存在。请检查 base_url 是否带正确路径 (大多数 provider 是 /v1)，"
        "以及 model id 是否拼写正确。"
    ),
    408: "请求超时，建议重试或缩短输入。",
    409: "请求冲突，可能并发或会话状态异常。",
    413: "请求体过大，请缩短 messages 或 max_tokens。",
    415: "Content-Type 不被支持，通常是 SDK 兼容性问题。",
    422: "请求参数校验失败 (Pydantic 风格)。请检查必填字段是否齐全。",
    429: "触发频率限制或配额耗尽，请稍后重试或升级套餐。",
    500: "上游服务内部错误，过一会再试。",
    502: "上游网关错误 (Bad Gateway)，通常会自愈。",
    503: "上游服务暂时不可用 (Service Unavailable)，过一会再试。",
    504: "上游网关超时 (Gateway Timeout)，过一会再试或增大 timeout。",
}


# Actionable next-step commands per status code. ``{name}`` is filled with the
# model's config name when the caller passes one, else a literal placeholder.
_NEXT_STEPS: dict[int, str] = {
    400: "用 `mbridge doctor model {name} --tools --verbose` 看哪个字段不被支持。",
    401: "运行 `mbridge model init` 重新填写 key，或 export 环境变量后用 `mbridge doctor model {name}` 验证。",
    402: "到模型控制台充值 / 开通付费，再 `mbridge doctor model {name}` 复测。",
    403: "在控制台为该 API Key 开通目标模型权限，再 `mbridge doctor model {name}`。",
    404: "用 `mbridge model list` 核对 model id 与 base_url，或 `mbridge model test {name}` 重新探测。",
    429: "等待数秒后重试，或换更省额度的模型 (`mbridge route \"...\" --mode economy`)。",
}
_NEXT_STEP_5XX = "上游故障，稍后重试；持续失败用 `mbridge doctor model {name}` 排查。"


def _format_next_step(status_code: int, model: str | None) -> str | None:
    """Pick the next-step template for ``status_code`` and fill in the name."""
    template = _NEXT_STEPS.get(status_code)
    if template is None and 500 <= status_code < 600:
        template = _NEXT_STEP_5XX
    if template is None:
        return None
    return template.replace("{name}", model or "<模型名>")


def hint_for_http_error(
    status_code: int,
    *,
    provider: str | None = None,
    body: str | None = None,
    model: str | None = None,
) -> str:
    """Return a Chinese hint for an HTTP error.

    Provider-specific clues from the response body are appended when they
    are likely to be helpful (e.g. mentions of ``reasoning_content``).
    A concrete "下一步" command suggestion is appended for common codes;
    ``model`` (the config name) is substituted into the suggested command
    when the caller has it on hand.
    """
    base = _STATUS_HINTS.get(status_code)
    if not base:
        if 400 <= status_code < 500:
            base = f"客户端错误 {status_code}。请检查请求字段、模型 ID 和 API Key。"
        elif 500 <= status_code < 600:
            base = f"服务端错误 {status_code}。建议稍后重试。"
        else:
            base = f"HTTP {status_code}。"

    extras: list[str] = []
    blob = (body or "").lower()
    if "reasoning_content" in blob:
        extras.append(
            "响应体提到 reasoning_content：thinking 模型的历史 assistant 消息必须保留 "
            "reasoning_content 并完整回传 (MiMo / Kimi-thinking / DeepSeek-reasoner 常见)。"
        )
    if "tool" in blob and ("not support" in blob or "unsupported" in blob or "invalid" in blob):
        extras.append("响应体提到 tools：该模型可能不支持工具调用，或 tools 字段格式不兼容。")
    if "json" in blob and ("not support" in blob or "invalid" in blob):
        extras.append("响应体提到 JSON：模型可能不支持 response_format={\"type\":\"json_object\"}。")
    if "model" in blob and ("not found" in blob or "does not exist" in blob or "invalid" in blob):
        extras.append("响应体提到 model：模型 ID 可能写错、未开通或区域受限。")
    if provider:
        extras.append(f"provider = {provider}")

    result = base + "  " + " ".join(extras) if extras else base

    next_step = _format_next_step(status_code, model)
    if next_step:
        result += f"  下一步: {next_step}"
    return result


def hint_for_exception(exc: BaseException, *, provider: str | None = None) -> str:
    """Return a Chinese hint for an exception raised before / outside HTTP."""
    if isinstance(exc, httpx.ConnectError):
        return (
            "连接失败。请检查 base_url 与网络。"
            "本地模型 (Ollama / vLLM / LM Studio) 需要确认服务已经启动，端口正确，"
            "并且 base_url 以 /v1 结尾 (Ollama 是 http://127.0.0.1:11434/v1)。"
        )
    if isinstance(exc, httpx.ReadTimeout):
        return "读取响应超时。本地模型可能太慢；可以增加 --timeout 或换用更小的模型。"
    if isinstance(exc, httpx.WriteTimeout):
        return "写入请求超时。检查网络稳定性或代理配置。"
    if isinstance(exc, httpx.ConnectTimeout):
        return "建立连接超时。检查 base_url、网络、代理或防火墙。"
    if isinstance(exc, httpx.ProxyError):
        return "代理错误。检查 HTTP(S)_PROXY / NO_PROXY 设置。"
    if isinstance(exc, httpx.TooManyRedirects):
        return "重定向过多。base_url 可能写错。"
    if isinstance(exc, httpx.HTTPError):
        return "HTTP 传输错误。检查网络与 base_url。"
    if isinstance(exc, ValueError):
        return "响应解析失败 (JSON)。endpoint 可能不是 OpenAI-compatible，或上游返回了 HTML 错误页。"
    name = type(exc).__name__
    if provider:
        return f"未预期错误 ({name})，provider = {provider}。"
    return f"未预期错误 ({name})。"


def hint_for_json_mode_failure(body_text: str | None) -> str:
    return (
        "JSON mode 测试失败。当前模型或 provider 可能不支持 OpenAI 标准的 "
        "response_format={\"type\":\"json_object\"}。可以在模型配置中关闭 capabilities.json，"
        "或在 prompt 中显式约束 JSON 格式。"
        + (f" 上游片段：{body_text[:200]}" if body_text else "")
    )


def hint_for_tool_call_failure(detail: str | None) -> str:
    return (
        "工具调用测试失败。该模型可能不支持 tool calls，或工具格式与 OpenAI 标准不一致，"
        "建议在配置中关闭 capabilities.tools，或等待对应 provider adapter 完善。"
        + (f" 详情：{detail}" if detail else "")
    )


def classify_error_type(exc_or_status: Any) -> str:
    """Map an exception or status code to one of our short error_type strings."""
    if isinstance(exc_or_status, int):
        if 400 <= exc_or_status < 500:
            return "http_4xx"
        if 500 <= exc_or_status < 600:
            return "http_5xx"
        return "unknown"
    if isinstance(exc_or_status, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        return "timeout"
    if isinstance(exc_or_status, httpx.ConnectError):
        return "connect"
    if isinstance(exc_or_status, ValueError):
        return "decode"
    if isinstance(exc_or_status, httpx.HTTPError):
        return "transport"
    return "unknown"
