"""Doctor: environment, single-model, and bulk model checks.

The doctor is the v0.2 centrepiece — it lets users figure out *why* a
particular national model is misbehaving without rolling their own
diagnostic script.

Three entry points (used by the CLI):

* :func:`run_global_doctor`    — ``mbridge doctor``
* :func:`run_model_doctor`     — ``mbridge doctor model NAME``
* :func:`run_doctor_all`       — ``mbridge doctor all``

Each returns structured results that the CLI renders with ``rich``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from .config import load_app_config, load_models_file
from .error_hints import hint_for_json_mode_failure, hint_for_tool_call_failure
from .models import ModelEntry
from .providers import ProviderError, get_provider
from .secrets import is_protected, reveal
from .schemas import ChatMessage, ChatRequest
from .utils import (
    get_app_dir,
    get_config_path,
    get_logs_dir,
    get_models_path,
    mask_secret,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    hint: str | None = None


@dataclass
class ModelDoctorReport:
    """Aggregated report for a single model — used by ``doctor all`` table."""

    name: str
    provider: str
    level: str
    chat_ok: bool = False
    chat_latency_ms: int | None = None
    has_reasoning: bool = False
    json_ok: bool | None = None
    tools_ok: bool | None = None
    status: str = "?"
    hints: list[str] = field(default_factory=list)
    results: list[CheckResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Global doctor
# ---------------------------------------------------------------------------

def run_global_doctor() -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(
        CheckResult(
            name="python version",
            ok=sys.version_info >= (3, 10),
            detail=sys.version.split()[0],
        )
    )

    app_dir = get_app_dir()
    results.append(CheckResult("app dir exists", app_dir.exists(), str(app_dir)))
    results.append(
        CheckResult("config.yaml exists", get_config_path().exists(), str(get_config_path()))
    )
    results.append(
        CheckResult("models.yaml exists", get_models_path().exists(), str(get_models_path()))
    )

    logs = get_logs_dir()
    try:
        logs.mkdir(parents=True, exist_ok=True)
        probe = logs / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        results.append(CheckResult("logs dir writable", True, str(logs)))
    except OSError as e:
        results.append(CheckResult("logs dir writable", False, str(logs), hint=str(e)))

    cfg = None
    try:
        cfg = load_app_config()
        results.append(
            CheckResult("config.yaml parses", True, f"default_model={cfg.default_model!r}")
        )
    except Exception as e:
        results.append(CheckResult("config.yaml parses", False, str(e)))

    mf = None
    try:
        mf = load_models_file()
        results.append(
            CheckResult("models.yaml parses", True, f"{len(mf.models)} model(s)")
        )
    except Exception as e:
        results.append(CheckResult("models.yaml parses", False, str(e)))

    if mf is not None:
        if mf.models:
            results.append(
                CheckResult(
                    "at least one model", True,
                    ", ".join(m.name for m in mf.models),
                )
            )
        else:
            results.append(
                CheckResult(
                    "at least one model", False, "models.yaml is empty",
                    hint="run `mbridge model init` to add one.",
                )
            )

    if cfg is not None and mf is not None and cfg.default_model:
        ok = any(m.name == cfg.default_model for m in mf.models)
        results.append(
            CheckResult(
                "default_model resolves", ok, cfg.default_model,
                hint=None if ok else "编辑 config.yaml 或 `mbridge model init` 添加同名模型。",
            )
        )

    results.append(_check_mcp_config())

    return results


def _check_mcp_config() -> CheckResult:
    """Static check of the ``mcp:`` config block — does not connect to servers.

    Connecting is what ``mbridge mcp list`` is for; the doctor only validates
    that the config parses and reports how many servers are enabled.
    """
    try:
        from .mcp import load_mcp_settings

        settings = load_mcp_settings()
    except Exception as e:
        return CheckResult(
            "mcp config", False, str(e),
            hint="检查 config.yaml 的 mcp 块；运行 `mbridge mcp list` 看详细连接状态。",
        )
    if not settings.servers:
        return CheckResult("mcp config", True, "未配置 MCP server（可选）")
    n_enabled = len(settings.enabled_servers())
    return CheckResult(
        "mcp config", True,
        f"{len(settings.servers)} 个 server（{n_enabled} 启用）· enabled={settings.enabled}",
        hint=None if settings.enabled else "mcp.enabled=false；REPL 不会接入这些 server。",
    )


def next_steps_for_global(results: list[CheckResult]) -> list[str]:
    msgs: list[str] = []
    by_name = {r.name: r for r in results}
    if not by_name.get("config.yaml exists", CheckResult("", False)).ok:
        msgs.append("运行 `mbridge init` 创建配置目录。")
        return msgs
    if not by_name.get("at least one model", CheckResult("", True)).ok:
        msgs.append("运行 `mbridge model init` 添加第一个模型。")
        return msgs
    dm = by_name.get("default_model resolves")
    if dm and not dm.ok:
        msgs.append("编辑 ~/.modelbridge/config.yaml 把 default_model 改成已存在的模型名。")
    if not msgs:
        msgs.append("一切就绪。运行 `mbridge doctor all` 检查每个模型，或 `mbridge ask \"你好\"`。")
    return msgs


# ---------------------------------------------------------------------------
# Single-model checks
# ---------------------------------------------------------------------------

def _check_config(entry: ModelEntry) -> CheckResult:
    issues = []
    if not entry.base_url:
        issues.append("base_url 为空")
    if not entry.model:
        issues.append("model 为空")
    if not entry.capabilities.local and not entry.api_key and not entry.api_key_env:
        issues.append("非本地模型但既没填 api_key 也没设 api_key_env")
    if issues:
        return CheckResult(
            "config completeness", False, "; ".join(issues),
            hint="编辑 ~/.modelbridge/models.yaml 或重新 `mbridge model init`。",
        )
    return CheckResult("config completeness", True, "OK")


def _check_api_key(entry: ModelEntry) -> CheckResult:
    if entry.capabilities.local:
        return CheckResult("api key", True, "local model — not required")
    # ``entry.api_key`` may be a keyring:/enc: token — reveal for display.
    key = reveal(entry.name, entry.api_key)
    if is_protected(entry.api_key):
        src = "keyring" if entry.api_key.startswith("keyring:") else "encrypted"
    else:
        src = "literal"
    if not key and entry.api_key_env:
        key = os.environ.get(entry.api_key_env, "")
        src = f"env:{entry.api_key_env}"
    if key:
        return CheckResult("api key", True, f"{src} → {mask_secret(key)}")
    hint = (
        f"未发现 api_key；如果配置了 api_key_env，请 `export {entry.api_key_env}=...`"
        if entry.api_key_env
        else "models.yaml 中未配置 api_key 或 api_key_env"
    )
    return CheckResult("api key", False, "missing", hint=hint)


def _check_health(provider) -> CheckResult:
    ok, detail = provider.health_check(timeout=5.0)
    return CheckResult(
        "endpoint reachable", ok, detail,
        hint=None if ok else "检查 base_url、网络、本地服务是否启动。",
    )


def _check_chat(provider, *, timeout: float, save_raw: bool) -> tuple[CheckResult, Any]:
    req = ChatRequest(
        model=provider.entry.model,
        messages=[ChatMessage(role="user", content="你好，请只回复 OK")],
        max_tokens=32,
    )
    try:
        resp = provider.chat(req, timeout=timeout, save_raw=save_raw, verbose_label="doctor")
    except ProviderError as e:
        return CheckResult("chat completion", False, e.message, hint=e.hint), None
    detail = f"{resp.elapsed_ms}ms, content={resp.content[:40]!r}"
    if resp.reasoning_content:
        detail += f", reasoning_content={len(resp.reasoning_content)}字符"
    return CheckResult("chat completion", True, detail), resp


def _check_json_mode(provider, *, timeout: float, save_raw: bool) -> CheckResult:
    req = ChatRequest(
        model=provider.entry.model,
        messages=[
            ChatMessage(role="user", content='请只返回 JSON：{"ok": true}')
        ],
        response_format={"type": "json_object"},
        max_tokens=32,
    )
    try:
        resp = provider.chat(req, timeout=timeout, save_raw=save_raw, verbose_label="doctor_json")
    except ProviderError as e:
        return CheckResult(
            "json mode", False, e.message,
            hint=hint_for_json_mode_failure(str(e.raw or "")),
        )

    text = (resp.content or "").strip()
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return CheckResult("json mode", True, f"parsed: {parsed}")
    return CheckResult(
        "json mode", False, f"非 JSON 输出：{text[:80]!r}",
        hint=hint_for_json_mode_failure(text),
    )


def _check_tools(provider, *, timeout: float, save_raw: bool) -> CheckResult:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo a value back. Use this when the user asks you to echo.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }
    ]
    req = ChatRequest(
        model=provider.entry.model,
        messages=[
            ChatMessage(role="user", content="请调用 echo 工具，把 value 设为 ping。"),
        ],
        tools=tools,
        tool_choice="auto",
        max_tokens=64,
    )
    try:
        resp = provider.chat(
            req, timeout=timeout, save_raw=save_raw, verbose_label="doctor_tools"
        )
    except ProviderError as e:
        return CheckResult(
            "tool calls", False, e.message,
            hint=hint_for_tool_call_failure(e.hint),
        )
    if resp.tool_calls:
        return CheckResult(
            "tool calls", True,
            f"{len(resp.tool_calls)} call(s); finish_reason={resp.finish_reason}",
        )
    return CheckResult(
        "tool calls", False, "模型未发起工具调用",
        hint=(
            "模型可能不支持 tools，或者本次没按要求调用。"
            "建议在 models.yaml 把 capabilities.tools 关掉，或换 coder/agent 等级的模型。"
        ),
    )


def run_model_doctor(
    entry: ModelEntry,
    *,
    test_tools: bool = False,
    save_raw: bool = False,
    timeout: float = 30.0,
) -> ModelDoctorReport:
    """Probe a single model. Returns a structured report for the CLI."""
    report = ModelDoctorReport(
        name=entry.name,
        provider=entry.provider.value,
        level=entry.level.value,
    )

    report.results.append(_check_config(entry))
    report.results.append(_check_api_key(entry))

    provider = get_provider(entry)
    report.results.append(_check_health(provider))

    chat_result, chat_resp = _check_chat(provider, timeout=timeout, save_raw=save_raw)
    report.results.append(chat_result)
    report.chat_ok = chat_result.ok
    if chat_resp is not None:
        report.chat_latency_ms = chat_resp.elapsed_ms
        report.has_reasoning = bool(chat_resp.reasoning_content)

    if chat_result.ok:
        if entry.capabilities.json:
            r = _check_json_mode(provider, timeout=timeout, save_raw=save_raw)
            report.results.append(r)
            report.json_ok = r.ok

        if test_tools and entry.capabilities.tools:
            r = _check_tools(provider, timeout=timeout, save_raw=save_raw)
            report.results.append(r)
            report.tools_ok = r.ok
        elif test_tools and not entry.capabilities.tools:
            report.results.append(
                CheckResult(
                    "tool calls", True,
                    "skipped (capabilities.tools=false)",
                    hint="在 models.yaml 把 capabilities.tools 改为 true 后再 retry。",
                )
            )

    report.status = "OK" if all(r.ok for r in report.results) else "FAIL"
    report.hints = [r.hint for r in report.results if r.hint]
    return report


# ---------------------------------------------------------------------------
# doctor all
# ---------------------------------------------------------------------------

def run_doctor_all(
    *, test_tools: bool = False, save_raw: bool = False, timeout: float = 20.0
) -> list[ModelDoctorReport]:
    mf = load_models_file()
    return [
        run_model_doctor(m, test_tools=test_tools, save_raw=save_raw, timeout=timeout)
        for m in mf.models
    ]


# Back-compat alias for v0.1 callers.
def run_doctor() -> list[CheckResult]:
    return run_global_doctor()
