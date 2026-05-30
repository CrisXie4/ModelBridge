"""Heuristic prompt → :class:`TaskProfile` classifier.

The old ``classify_level`` / :class:`RoutingDecision` API is preserved for
backwards compatibility (existing router / CLI code keeps working);
the richer :func:`classify_task` returns a :class:`TaskProfile` with
``task_type`` / ``complexity`` / ``risk_level`` / ``recommended_level``
/ ``reasons`` for ``mbridge route`` and ``mbridge route test``.

Rules are deliberately conservative — we'd rather route up than down
(silently downgrading to a tiny model can produce garbage), so ties
break toward the higher level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..models import ModelLevel


# ---------------------------------------------------------------------------
# TaskProfile — phase-3 richer output
# ---------------------------------------------------------------------------

TaskType = Literal[
    "chat",
    "explain",
    "code_explain",
    "code_generate",
    "code_edit",
    "debug",
    "architecture",
    "security_review",
    "refactor",
    "agent_task",
    "unknown",
]
Complexity = Literal["simple", "medium", "hard"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass
class TaskProfile:
    """Classifier verdict — explains *what* the user is asking and *why*.

    ``recommended_level`` is what the router should consult before any
    mode shift; the router may move it up/down depending on
    economy / balanced / powerful mode.
    """

    task_type: TaskType
    complexity: Complexity
    recommended_level: ModelLevel
    reasons: list[str] = field(default_factory=list)
    risk_level: RiskLevel = "low"
    matched_keywords: list[str] = field(default_factory=list)
    length: int = 0


# ---------------------------------------------------------------------------
# Task-type keyword anchors. Order: most specific → least specific.
# Each entry: (task_type, complexity, level, risk, keywords)
# ---------------------------------------------------------------------------

_TASK_RULES: list[tuple[TaskType, Complexity, ModelLevel, RiskLevel, list[str]]] = [
    # ------ security: high risk, always expert ----------------------------
    ("security_review", "hard", ModelLevel.EXPERT, "high", [
        "安全", "漏洞", "权限", "密钥", "注入", "secret", "credentials",
        "security review", "security audit", "vulnerability", "injection",
        "xss", "csrf", "sql injection",
    ]),
    # ------ architecture / refactor: hard, expert -------------------------
    ("architecture", "hard", ModelLevel.EXPERT, "medium", [
        "整个项目", "全工程", "架构", "设计", "重构", "重写", "多文件",
        "项目架构", "performance optimization",
        "refactor", "redesign", "architecture", "rewrite this project",
        "multi-file", "project-wide", "design pattern",
    ]),
    # ------ agent / tools / mcp ------------------------------------------
    ("agent_task", "hard", ModelLevel.AGENT, "medium", [
        "mcp", "tool call", "tool_call", "调用工具", "工具调用",
        "自动执行", "browse", "run tests", "agent",
    ]),
    # ------ debug / fix bug ----------------------------------------------
    ("debug", "medium", ModelLevel.CODER, "medium", [
        "修复", "修一下", "改一下", "fix bug", "fix the bug", "debug",
        "排查", "为什么会报错", "调试", "stack trace", "traceback",
    ]),
    # ------ code edit / patch / diff -------------------------------------
    ("code_edit", "medium", ModelLevel.CODER, "medium", [
        "修改文件", "改文件", "生成 diff", "生成diff", "改这个项目",
        "edit file", "patch", "apply diff", "rewrite this file",
    ]),
    # ------ code generate -------------------------------------------------
    ("code_generate", "medium", ModelLevel.CODER, "low", [
        "写代码", "实现", "生成代码", "写一个", "写函数", "写一段",
        "帮我写", "write a", "implement", "generate code", "code for me",
        "function", "class",
    ]),
    # ------ code explain --------------------------------------------------
    ("code_explain", "simple", ModelLevel.CHEAP, "low", [
        "这段代码", "这个函数", "这个类", "代码解释",
        "explain this code", "what does this code do", "what does this function",
    ]),
    # ------ explain / report errors --------------------------------------
    ("explain", "simple", ModelLevel.CHEAP, "low", [
        "什么是", "解释一下", "怎么用", "什么意思", "为什么",
        "报错是什么意思", "总结", "翻译",
        "what is", "explain", "what does it mean", "why does",
        "summarize", "translate", "error message",
    ]),
    # ------ classification / tiny ----------------------------------------
    ("chat", "simple", ModelLevel.TINY, "low", [
        "判断", "分类", "意图", "是不是", "是否",
        "classify", "intent", "yes or no", "is this",
    ]),
]


_CODE_FENCE = re.compile(r"```", re.MULTILINE)
_FILE_GLOB = re.compile(
    r"\b[\w./\-]+\.(py|ts|tsx|js|jsx|go|rs|java|cpp|c|h|md|yaml|yml|json|toml)\b"
)


_LEVEL_ORDER = [
    ModelLevel.TINY,
    ModelLevel.CHEAP,
    ModelLevel.CODER,
    ModelLevel.AGENT,
    ModelLevel.EXPERT,
]


def _bump_level(level: ModelLevel, steps: int) -> ModelLevel:
    """Move level up (positive) or down (negative) along the order."""
    idx = _LEVEL_ORDER.index(level)
    idx = max(0, min(len(_LEVEL_ORDER) - 1, idx + steps))
    return _LEVEL_ORDER[idx]


def _bump_complexity(c: Complexity, steps: int) -> Complexity:
    order: list[Complexity] = ["simple", "medium", "hard"]
    idx = max(0, min(len(order) - 1, order.index(c) + steps))
    return order[idx]


# ---------------------------------------------------------------------------
# Public: classify_task
# ---------------------------------------------------------------------------

def classify_task(
    prompt: str,
    *,
    has_files: bool = False,
    wants_edit: bool = False,
    wants_tools: bool = False,
    wants_mcp: bool = False,
    context_tokens: int = 0,
    previous_failures: int = 0,
) -> TaskProfile:
    """Classify a prompt into a :class:`TaskProfile`.

    The optional kwargs carry context the caller may have on hand. The
    classifier never *requires* them — passing only ``prompt`` still works.
    """
    text = (prompt or "").strip()
    length = len(text)
    reasons: list[str] = []
    matched: list[str] = []

    if not text:
        return TaskProfile(
            task_type="unknown",
            complexity="simple",
            recommended_level=ModelLevel.TINY,
            reasons=["empty prompt"],
            risk_level="low",
            length=0,
        )

    # ---- keyword scan: walk most-specific → least-specific, take first hit
    chosen: tuple[TaskType, Complexity, ModelLevel, RiskLevel] | None = None
    lowered = text.lower()
    for task_type, complexity, level, risk, words in _TASK_RULES:
        hits = [w for w in words if w in text or w.lower() in lowered]
        if hits:
            matched.extend(hits[:3])
            reasons.append(
                f"命中 {task_type} 关键词: {', '.join(hits[:3])}"
            )
            chosen = (task_type, complexity, level, risk)
            break

    if chosen is None:
        # No keyword fired — default to "chat / simple / cheap".
        chosen = ("chat", "simple", ModelLevel.CHEAP, "low")
        reasons.append("无显著关键词 → 默认 chat / cheap")

    task_type, complexity, level, risk = chosen

    # ---- structural signals: code fence / file paths bump the level ------
    if _CODE_FENCE.search(text):
        reasons.append("含代码块 (``` fence)")
        level = max(level, ModelLevel.CODER, key=_LEVEL_ORDER.index)
        if complexity == "simple":
            complexity = "medium"

    file_hits = _FILE_GLOB.findall(text)
    if len(file_hits) >= 2:
        reasons.append(f"提到 ≥2 个源文件后缀 ({len(file_hits)})")
        level = max(level, ModelLevel.AGENT, key=_LEVEL_ORDER.index)
        complexity = "hard"
    elif len(file_hits) == 1:
        reasons.append("提到 1 个源文件后缀")
        level = max(level, ModelLevel.CODER, key=_LEVEL_ORDER.index)

    # ---- length signal ---------------------------------------------------
    if length >= 1200:
        reasons.append(f"prompt 较长 ({length} 字符)")
        level = max(level, ModelLevel.AGENT, key=_LEVEL_ORDER.index)
        complexity = "hard"
    elif length <= 20 and task_type == "chat":
        # Very short generic chat stays tiny.
        level = ModelLevel.TINY

    # ---- context-tokens signal ------------------------------------------
    if context_tokens > 32000:
        reasons.append(f"上下文 tokens 较大 ({context_tokens})")
        level = max(level, ModelLevel.AGENT, key=_LEVEL_ORDER.index)
        complexity = "hard"

    # ---- caller-supplied hints ------------------------------------------
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

    # ---- previous failures → escalate to expert -------------------------
    if previous_failures >= 2:
        reasons.append(f"previous_failures={previous_failures} → expert")
        level = ModelLevel.EXPERT
        complexity = "hard"
        risk = _max_risk(risk, "medium")

    return TaskProfile(
        task_type=task_type,
        complexity=complexity,
        recommended_level=level,
        reasons=reasons,
        risk_level=risk,
        matched_keywords=matched,
        length=length,
    )


def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    order: list[RiskLevel] = ["low", "medium", "high"]
    return order[max(order.index(a), order.index(b))]


# ---------------------------------------------------------------------------
# Legacy API — kept so existing callers (router.py, tests) keep working.
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    """Legacy verdict — thin wrapper around :class:`TaskProfile`."""

    level: ModelLevel
    reasons: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    length: int = 0


def classify_level(prompt: str) -> RoutingDecision:
    """Legacy classifier — delegates to :func:`classify_task`."""
    p = classify_task(prompt)
    return RoutingDecision(
        level=p.recommended_level,
        reasons=p.reasons,
        matched_keywords=p.matched_keywords,
        length=p.length,
    )
