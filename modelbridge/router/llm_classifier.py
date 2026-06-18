"""LLM-driven prompt classifier — ask the *lowest-tier* model itself.

Where :func:`modelbridge.router.classifier.classify_task` reads keywords
and structural signals, this module hands the decision to the model: it
resolves the ``tiny`` level to a concrete model, sends a tight
classification prompt, and parses a strict-JSON verdict back into a
:class:`TaskProfile`.

Design contract (chosen deliberately, see project decision):

* **LLM-only.** There is *no* silent fall back to the keyword
  classifier. If the tiny model is unconfigured / unreachable / returns
  garbage, we raise :class:`LLMClassifyError` so the caller can surface a
  clear error instead of routing on a guess.
* The model decides ``task_type`` / ``complexity`` / ``risk_level`` /
  ``recommended_level``. Explicit *caller facts* (``wants_edit`` etc.) are
  still applied as floors afterwards — those aren't keyword guessing, and
  keeping them keeps parity with the keyword path for later wiring.
"""

from __future__ import annotations

import json
from typing import get_args

from ..client import ChatError, chat_once
from ..models import ModelLevel
from ..providers import ProviderError
from .classifier import (
    _LEVEL_ORDER,  # noqa: PLC2701 - same-package reuse
    Complexity,
    RiskLevel,
    TaskProfile,
    TaskType,
    _max_risk,  # noqa: PLC2701
)
from .fallback import resolve_with_fallback


class LLMClassifyError(Exception):
    """Raised when the tiny model can't produce a usable classification."""


# Valid enum values, derived from the Literal types so they stay in sync.
_VALID_TASK_TYPES: frozenset[str] = frozenset(get_args(TaskType))
_VALID_COMPLEXITY: frozenset[str] = frozenset(get_args(Complexity))
_VALID_RISK: frozenset[str] = frozenset(get_args(RiskLevel))
_VALID_LEVELS: frozenset[str] = frozenset(m.value for m in ModelLevel)


_CLASSIFIER_SYSTEM = """\
你是 ModelBridge 的请求分级器。给定一段用户请求，判断它应该路由到哪一等级的模型，并输出严格 JSON。

等级 (recommended_level) 从低到高：
- tiny:   意图分类 / 是非判断 / 极简预处理
- cheap:  普通问答 / 解释报错 / 简单代码解释
- coder:  单文件代码生成 / 简单 bug 修复 / 生成 diff
- agent:  多文件任务 / 需要工具调用 / MCP
- expert: 架构重构 / 安全审查 / 高风险或多次失败的兜底

字段取值（必须从给定集合里选一个）：
- task_type:  chat, explain, code_explain, code_generate, code_edit, debug, architecture, security_review, refactor, agent_task, unknown
- complexity: simple, medium, hard
- risk_level: low, medium, high   （涉及安全 / 漏洞 / 密钥 / 权限 / 注入 → high）

只输出一个 JSON 对象，不要任何解释、不要 markdown、不要代码围栏：
{"task_type": "...", "complexity": "...", "risk_level": "...", "recommended_level": "...", "reason": "一句话中文理由"}\
"""


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Tolerates the model wrapping the JSON in prose or a ```json fence;
    raises :class:`LLMClassifyError` if nothing parseable is found.
    """
    raw = (text or "").strip()
    if not raw:
        raise LLMClassifyError("分级模型返回空内容")

    # Fast path: the whole reply is JSON.
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: decode the first complete JSON object starting at the first
    # '{'. raw_decode stops at the end of that object, so trailing prose or a
    # second object is ignored, and nested objects are handled correctly.
    start = raw.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise LLMClassifyError(f"无法从分级模型响应解析出 JSON：{raw[:200]!r}")


def classify_task_llm(
    prompt: str,
    *,
    has_files: bool = False,
    wants_edit: bool = False,
    wants_tools: bool = False,
    wants_mcp: bool = False,
    context_tokens: int = 0,  # noqa: ARG001 - accepted for signature parity
    previous_failures: int = 0,
    timeout: float = 30.0,
) -> TaskProfile:
    """Classify ``prompt`` by asking the tiny model. LLM-only, no fallback.

    Raises:
        LLMClassifyError: tiny model unconfigured / unreachable / unparseable.
    """
    text = (prompt or "").strip()
    if not text:
        # Empty prompt needs no model call — mirror the keyword classifier.
        return TaskProfile(
            task_type="unknown",
            complexity="simple",
            recommended_level=ModelLevel.TINY,
            reasons=["empty prompt"],
            risk_level="low",
            length=0,
        )

    # ---- resolve the lowest-tier model ----------------------------------
    fb = resolve_with_fallback(ModelLevel.TINY)
    model_name = fb.chosen_model
    if not model_name:
        raise LLMClassifyError(
            "未解析到可用的最低层 (tiny) 模型；检查 config.yaml 的 "
            "routing.levels.tiny 与 models.yaml。"
        )

    # ---- call the model -------------------------------------------------
    try:
        _entry, resp = chat_once(
            f"请对下面这段请求分级：\n\n{text}",
            model_name=model_name,
            system=_CLASSIFIER_SYSTEM,
            timeout=timeout,
            verbose_label="route_classify",
        )
    except (ProviderError, ChatError) as e:
        raise LLMClassifyError(
            f"调用分级模型 '{model_name}' 失败：{e}"
        ) from e

    data = _extract_json(resp.content)

    # ---- validate every field against its enum --------------------------
    task_type = str(data.get("task_type", "")).strip().lower()
    complexity = str(data.get("complexity", "")).strip().lower()
    risk_level = str(data.get("risk_level", "")).strip().lower()
    level_str = str(data.get("recommended_level", "")).strip().lower()
    model_reason = str(data.get("reason", "")).strip()

    if task_type not in _VALID_TASK_TYPES:
        raise LLMClassifyError(f"分级模型返回了未知 task_type：{task_type!r}")
    if complexity not in _VALID_COMPLEXITY:
        raise LLMClassifyError(f"分级模型返回了未知 complexity：{complexity!r}")
    if risk_level not in _VALID_RISK:
        raise LLMClassifyError(f"分级模型返回了未知 risk_level：{risk_level!r}")
    if level_str not in _VALID_LEVELS:
        raise LLMClassifyError(f"分级模型返回了未知 recommended_level：{level_str!r}")

    level = ModelLevel(level_str)
    reasons: list[str] = [f"LLM 分级 (模型 {model_name}): {model_reason or '(无理由)'}"]

    # ---- caller-fact floors (explicit signals, not keyword guessing) ----
    if has_files:
        reasons.append("has_files=true")
        level = max(level, ModelLevel.CODER, key=_LEVEL_ORDER.index)
    if wants_edit:
        reasons.append("wants_edit=true")
        level = max(level, ModelLevel.CODER, key=_LEVEL_ORDER.index)
        if task_type in ("chat", "explain"):
            task_type = "code_edit"
    if wants_tools or wants_mcp:
        reasons.append("wants_tools/mcp=true")
        level = max(level, ModelLevel.AGENT, key=_LEVEL_ORDER.index)
        if task_type in ("chat", "explain"):
            task_type = "agent_task"
    if previous_failures >= 2:
        reasons.append(f"previous_failures={previous_failures} → expert")
        level = ModelLevel.EXPERT
        complexity = "hard"
        risk_level = _max_risk(risk_level, "medium")  # type: ignore[arg-type]

    return TaskProfile(
        task_type=task_type,  # type: ignore[arg-type]
        complexity=complexity,  # type: ignore[arg-type]
        recommended_level=level,
        reasons=reasons,
        risk_level=risk_level,  # type: ignore[arg-type]
        matched_keywords=[],
        length=len(text),
    )
