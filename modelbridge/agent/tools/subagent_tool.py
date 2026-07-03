"""spawn_subagent tool — let the AI spin up a focused sub-agent for a sub-task.

Design contract
--------------
* The parent model calls ``spawn_subagent`` with a natural-language *goal*.
* The tool asks the user for confirmation before doing anything.
* If the user picks **always** the pattern (goal hash) is persisted so future
  similar goals auto-confirm.
* The sub-agent runs **one shot** — one ``run_agent_turn`` with a bounded
  ``max_iters`` so it can't run forever.
* The parent's session is **not** mutated; the sub-agent gets its own
  ``Session`` seeded with the goal as a user message.
* The parent sees a rich ``ToolResult`` summarising what the sub-agent did.
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
# Tool
# ---------------------------------------------------------------------------

_SUBAGENT_SYSTEM = """\
你是一个专注于执行单一任务的 AI 子智能体。
你的任务：{goal}
不要主动调用工具（除了必要的文件读写），专注于完成任务。
完成后，用中文清晰描述你做了什么、结果是什么。\
"""


class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "派生子智能体处理一个独立子任务。\n"
        "goal: 子任务描述（越详细越好，AI 会据此决定是否需要你亲自处理）\n"
        "model (可选): 指定子智能体使用的模型，默认跟随父智能体\n"
        "max_iters (可选): 最大工具调用轮次，默认 10\n\n"
        "⚠ 高风险操作，会弹出确认框。"
    )

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "子任务目标，用中文详细描述",
                },
                "model": {
                    "type": "string",
                    "description": "子智能体使用的模型（默认：父模型）",
                },
                "max_iters": {
                    "type": "integer",
                    "description": "最大工具调用轮次（默认 10）",
                    "default": 10,
                },
            },
            "required": ["goal"],
        }

    def execute(self, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        goal: str = args.get("goal", "")
        model: str | None = args.get("model")
        max_iters: int = args.get("max_iters", 10)

        if not goal.strip():
            return self.err("goal 不能为空")

        # ---- Check persistent approval first --------------------------------
        h = _goal_hash(goal)
        approved = _load_approved_patterns()
        persistent_ok = approved.get(h)

        # Pattern key used for "always" persistence
        pattern_key = f"subagent:{h}"

        # Short-circuit under --yes or persistent approval
        if persistent_ok:
            pass  # silently proceed
        elif ctx.confirm(
            tool=self.name,
            summary=f"AI 申请派生子智能体：{goal[:60]}{'…' if len(goal) > 60 else ''}",
            detail=(
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
                model_name=model,
                max_iters=max_iters,
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
            "✅ 子智能体完成",
            f"目标    : {goal}",
            f"模型    : {result.model_name}",
            f"执行轮次: {result.turns}",
            f"工具调用: {tool_list}",
            f"\n--- 子智能体输出 ---\n{result.final_content}",
        ]

        return self.ok(
            "\n".join(summary_lines),
            structured={
                "goal": goal,
                "model": result.model_name,
                "turns": result.turns,
                "tool_calls": result.tool_calls,
            },
        )


def _run_subagent(
    *,
    goal: str,
    model_name: str | None,
    max_iters: int,
    parent_ctx: AgentContext,
) -> SubAgentResult:
    """Execute a sub-agent synchronously (blocking the parent loop)."""
    # Resolve model (entry lookup kept for early validation; actual model
    # resolution happens later in run_agent_turn)
    if not model_name:
        # Inherit parent's model by default
        model_name = getattr(parent_ctx, "_parent_model", None) or "deepseek-chat"
    try:
        get_model_entry(model_name)
    except ChatError as e:
        return SubAgentResult(
            goal=goal, model_name=model_name or "(default)",
            turns=0, tool_calls=[], final_content="",
            error=f"模型不可用: {e}",
        )

    # Build a fresh session for the sub-agent
    session = Session(model_name=model_name)
    session.add_system(_SUBAGENT_SYSTEM.format(goal=goal))
    session.add_user(goal)

    # Sub-agents get a restricted tool set: read/list only (safe)
    # Write/edit are NOT included to prevent the sub-agent from modifying files
    # without going through the parent's approval flow
    from .file_tools import ReadFileTool, ListDirTool
    sub_reg = ToolRegistry()
    sub_reg.register(ReadFileTool())
    sub_reg.register(ListDirTool())

    # Give the sub-agent its own minimal security context
    from ..security import PathPolicy
    sub_policy = PathPolicy(
        allowed_dirs=[parent_ctx.cwd],
        blocked_patterns=["*.exe", "*.dll", "*.so", "*.pem", "*.key", "*.env"],
    )

    # Sub-agent approval: always YES (user already confirmed at parent level)
    from ..context import auto_yes
    sub_ctx = AgentContext(
        policy=sub_policy,
        cwd=parent_ctx.cwd,
        approve=auto_yes,
        allow_bash=False,
        model_is_vision=getattr(parent_ctx, "model_is_vision", False),
    )
    # Tag so parent's model can be retrieved if sub wants to delegate further
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
