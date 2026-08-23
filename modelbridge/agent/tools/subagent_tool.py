"""spawn_subagent tool — let the AI spin up a focused sub-agent for a sub-task.

Built-in agent kinds (with their own prompts + toolsets):

* ``explore`` (default) — read-only scouting: grep/list/read with a
  locate-first, evidence-with-path:line prompt. No confirmation needed:
  it's no more capable than the parent's own read tools, confined to the
  same PathPolicy.
* ``general`` — focused execution: read/write/edit (write tools still
  confirm through the *parent's* approval channel, so the user keeps
  control). One spawn confirmation up front (y/N/a, "always" persisted).

Design contract
---------------
* The parent model calls ``spawn_subagent`` with a natural-language *goal*.
* The sub-agent runs **one shot** — one ``run_agent_turn`` with a bounded
  ``max_iters`` so it can't run forever. Its session is fresh; the parent's
  session is untouched.
* The parent sees a ``ToolResult`` summarising what the sub-agent did plus
  its final report. Sub-agents never nest (no spawn_subagent in their
  registry), so delegation depth is capped at one level.
* Sub-agent sessions get the same seamless compaction as everything else
  (run_agent_turn pressure checks), so a long exploration can't blow its
  own context window.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...client import ChatError, get_model_entry
from ..context import AgentContext
from ..session import Session
from ..loop import run_agent_turn
from .base import Tool, ToolResult
from .registry import ToolRegistry


# ---------------------------------------------------------------------------
# Persistent approval store
# ---------------------------------------------------------------------------

_APPROVED_PATTERNS_FILE = "approved_subagent_patterns.json"


def _approved_patterns_path() -> Path:
    from ...utils import get_app_dir
    return get_app_dir() / _APPROVED_PATTERNS_FILE


def _load_approved_patterns() -> dict[str, str]:
    """Return {goal_hash → label} for permanently approved subagent patterns."""
    p = _approved_patterns_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_approved_patterns(patterns: dict[str, str]) -> None:
    p = _approved_patterns_path()
    try:
        p.write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # non-fatal — worst case user re-approves


def _goal_hash(goal: str) -> str:
    """Deterministic hash so the same goal pattern always matches."""
    return hashlib.sha256(goal.strip().encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# SubAgentResult — what a completed sub-agent returns
# ---------------------------------------------------------------------------

@dataclass
class SubAgentResult:
    goal: str
    model_name: str
    turns: int
    tool_calls: list[str]
    final_content: str
    error: str | None = None


# ---------------------------------------------------------------------------
# Built-in agent kinds: prompt + toolset + defaults
# ---------------------------------------------------------------------------

_EXPLORE_SYSTEM = """\
你是 ModelBridge 的探索子智能体 (explore agent)，负责为父智能体完成一次只读的代码/资料侦察。

任务目标：
{goal}

工作方式：
- 用 grep 定位符号/字符串，用 list_dir 浏览结构，用 read_file 精读关键文件。
- 只读：不修改任何文件。
- 广度优先：先定位所有相关位置，再深读最相关的 2-3 处；一次没找到就换关键词/换路径再试。
- 每个结论都要有证据：文件路径 + 行号（如 modelbridge/agent/loop.py:42）。

最终回复格式（直接给结果，不要寒暄）：
1. 结论摘要（3-5 条）
2. 关键位置清单（path:line — 一句话说明作用）
3. 与目标直接相关的代码片段（仅在有助于理解时）
4. 不确定 / 未找到的部分，明确说明\
"""

_GENERAL_SYSTEM = """\
你是 ModelBridge 的通用子智能体 (general agent)，独立完成父智能体交给你的一个聚焦子任务。

任务目标：
{goal}

工作方式：
- 可以读、写、编辑文件（写操作仍会请求用户确认，请如实填写 reason）。
- 子任务自包含：独立完成后把结果汇报给父智能体，不要请求父智能体代劳。
- 遇到阻塞（缺信息 / 需要决策）不要猜——在输出里说明并给选项。
- 改动尽量小而精确：优先 str_replace，慎用整文件重写。

最终回复格式（直接给结果，不要寒暄）：
1. 完成了什么（对照目标逐条）
2. 改动的文件 + 每处一句话说明
3. 验证方式与结果（跑了什么命令/测试）
4. 遗留问题或建议\
"""

#: kind → built-in configuration. Toolsets deliberately exclude browser /
#: computer control / nested spawn_subagent — a sub-agent is scoped to the
#: filesystem around the parent's cwd.
AGENT_KINDS: dict[str, dict[str, Any]] = {
    "explore": {
        "system": _EXPLORE_SYSTEM,
        "tools": ("read_file", "list_dir", "grep"),
        "default_max_iters": 15,
        "confirm": False,        # read-only + PathPolicy-confined
        "label": "探索 (只读侦察，无需确认)",
    },
    "general": {
        "system": _GENERAL_SYSTEM,
        "tools": ("read_file", "list_dir", "grep", "write_file", "str_replace"),
        "default_max_iters": 10,
        "confirm": True,         # carries write tools
        "label": "通用 (可写文件，写操作仍逐次确认)",
    },
}
DEFAULT_KIND = "explore"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "派生一个内置子智能体处理独立子任务（有自己的系统提示词和工具集）。\n"
        "kind=explore (默认): 只读侦察 — grep/list/read，定位代码、找配置、"
        "查结构，返回带 path:line 证据的结论；无需确认。\n"
        "kind=general: 聚焦执行 — 可读可写（写操作仍逐次请求用户确认）。\n"
        "goal: 子任务目标，写得越具体越好（上下文、范围、期望输出）。\n"
        "model (可选): 子智能体使用的模型，默认跟随父智能体。\n"
        "max_iters (可选): 最大工具轮次（explore 默认 15，general 默认 10）。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "子任务目标，用中文详细描述",
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(AGENT_KINDS),
                    "description": f"内置智能体类型（默认 {DEFAULT_KIND}）",
                    "default": DEFAULT_KIND,
                },
                "model": {
                    "type": "string",
                    "description": "子智能体使用的模型（默认：父模型）",
                },
                "max_iters": {
                    "type": "integer",
                    "description": "最大工具调用轮次（默认按 kind）",
                },
            },
            "required": ["goal"],
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        goal: str = args.get("goal", "")
        kind: str = args.get("kind") or DEFAULT_KIND
        model: str | None = args.get("model")
        kind_cfg = AGENT_KINDS.get(kind)
        if kind_cfg is None:
            return self.err(
                f"未知 kind: {kind}。可选: {', '.join(sorted(AGENT_KINDS))}"
            )
        max_iters: int = args.get("max_iters") or kind_cfg["default_max_iters"]

        if not goal.strip():
            return self.err("goal 不能为空")

        # ---- Confirmation (only kinds that can mutate / cost a lot) --------
        if kind_cfg["confirm"]:
            h = _goal_hash(f"{kind}:{goal}")
            approved = _load_approved_patterns()
            persistent_ok = approved.get(h)
            pattern_key = f"subagent:{h}"

            # Short-circuit under persistent approval
            if persistent_ok:
                pass  # silently proceed
            elif ctx.confirm(
                tool=self.name,
                summary=(
                    f"AI 申请派生 {kind} 子智能体："
                    f"{goal[:60]}{'…' if len(goal) > 60 else ''}"
                ),
                detail=(
                    f"类型    : {kind} — {kind_cfg['label']}\n"
                    f"目标    : {goal}\n"
                    f"模型    : {model or '(跟随父智能体)'}\n"
                    f"最大轮次: {max_iters}\n\n"
                    "[dim]选择 [a]lways 可永久批准此类目标，以后自动通过。[/dim]"
                ),
                allow_always=True,
                pattern_key=pattern_key,
                auto=True,
            ):
                pass  # YES or ALWAYS (ALWAYS already persisted by cli._make_approval)
            else:
                return self.err("用户拒绝了子智能体请求")

        # ---- Run the sub-agent -----------------------------------------------
        try:
            result = _run_subagent(
                goal=goal,
                kind=kind,
                model_name=model,
                max_iters=int(max_iters),
                parent_ctx=ctx,
            )
        except Exception as e:
            return self.err(f"子智能体执行失败: {e}")

        if result.error:
            return self.ok(
                f"子智能体执行出错：{result.error}\n\n目标：{goal}",
                structured={"subagent_error": result.error},
            )

        # Build summary
        tool_list = ", ".join(result.tool_calls) if result.tool_calls else "(无工具调用)"
        summary_lines = [
            f"✅ {kind} 子智能体完成",
            f"目标    : {goal}",
            f"模型    : {result.model_name}",
            f"执行轮次: {result.turns}",
            f"工具调用: {tool_list}",
            f"\n--- 子智能体输出 ---\n{result.final_content}",
        ]

        return self.ok(
            "\n".join(summary_lines),
            structured={
                "kind": kind,
                "goal": goal,
                "model": result.model_name,
                "turns": result.turns,
                "tool_calls": result.tool_calls,
            },
        )


def _run_subagent(
    *,
    goal: str,
    kind: str,
    model_name: str | None,
    max_iters: int,
    parent_ctx: AgentContext,
) -> SubAgentResult:
    """Execute a sub-agent synchronously (blocking the parent loop)."""
    kind_cfg = AGENT_KINDS[kind]
    # Inherit the parent's model by default; fall back to the default model.
    if not model_name:
        model_name = (
            getattr(parent_ctx, "_parent_model", None)
            or _default_model_name()
            or "deepseek-v4-flash"
        )
    try:
        get_model_entry(model_name)
    except ChatError as e:
        return SubAgentResult(
            goal=goal, model_name=model_name or "(default)",
            turns=0, tool_calls=[], final_content="",
            error=f"模型不可用: {e}",
        )

    # Build a fresh session seeded with the kind's built-in prompt.
    session = Session(model_name=model_name)
    session.add_system(kind_cfg["system"].format(goal=goal))
    session.add_user(goal)

    # Kind-scoped registry. read-only kinds stay read-only; write-capable
    # kinds forward every write confirmation to the parent's approval
    # channel (terminal y/N/a in the CLI, card in the side panel), so the
    # user keeps exactly the control they'd have without delegation.
    from .file_tools import (
        GrepTool,
        ListDirTool,
        ReadFileTool,
        StrReplaceTool,
        WriteFileTool,
    )

    # dict[str, Any]: 值是各工具类；收窄到基类会让实例化报 abstract。
    tools_by_name: dict[str, Any] = {
        "read_file": ReadFileTool,
        "list_dir": ListDirTool,
        "grep": GrepTool,
        "write_file": WriteFileTool,
        "str_replace": StrReplaceTool,
    }
    sub_reg = ToolRegistry()
    for tname in kind_cfg["tools"]:
        sub_reg.register(tools_by_name[tname]())

    from ..security import PathPolicy
    sub_policy = PathPolicy(
        allowed_dirs=[parent_ctx.cwd],
        blocked_patterns=["*.exe", "*.dll", "*.so", "*.pem", "*.key", "*.env"],
    )

    if kind_cfg["confirm"]:
        approve = parent_ctx.approve  # writes confirm through the parent
    else:
        from ..context import auto_yes  # read-only: nothing to approve

        approve = auto_yes

    sub_ctx = AgentContext(
        policy=sub_policy,
        cwd=parent_ctx.cwd,
        approve=approve,
        allow_bash=False,
        model_is_vision=getattr(parent_ctx, "model_is_vision", False),
    )
    sub_ctx._parent_model = model_name  # type: ignore[attr-defined]

    # Run one turn (blocking)
    agent_result = run_agent_turn(
        session=session,
        ctx=sub_ctx,
        registry=sub_reg,
        model_name=model_name,
        max_iters=max_iters,
        stream=False,
    )

    if agent_result.stopped_reason == "provider_error" and agent_result.error:
        return SubAgentResult(
            goal=goal,
            model_name=model_name,
            turns=agent_result.iterations,
            tool_calls=[tc.name for tc in agent_result.tool_calls_executed],
            final_content="",
            error=f"模型调用失败: {agent_result.error.message}",
        )

    final = agent_result.final_response
    tool_names = [tc.name for tc in agent_result.tool_calls_executed]
    content = final.content if final else "(无输出)"

    return SubAgentResult(
        goal=goal,
        model_name=model_name,
        turns=agent_result.iterations,
        tool_calls=tool_names,
        final_content=content,
        error=None,
    )


def _default_model_name() -> str | None:
    """Resolve the configured default model without raising."""
    try:
        from ...client import resolve_model_name

        return resolve_model_name(None)
    except Exception:
        return None
