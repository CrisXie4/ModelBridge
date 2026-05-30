"""Generate ``AGENT.md`` for a project.

Pipeline
--------
1. :func:`modelbridge.project.scan_project` builds a structured summary.
2. We compose a tight prompt asking the model to emit AGENT.md sections
   (Project Overview / Tech Stack / Common Commands / Directory Structure
   / Coding Rules / Agent Instructions / Safety Rules / Known Notes).
3. :func:`modelbridge.client.chat_once` sends the request.
4. The caller writes the response to ``<project>/AGENT.md`` (with
   ``--force`` / ``--yes`` gating) — we deliberately don't touch the
   filesystem here; this module is pure compute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..client import chat_once
from .scanner import ProjectSummary, scan_project


# Output target inside the project.
DEFAULT_TARGET_FILENAME = "AGENT.md"


# ---------------------------------------------------------------------------
# Prompt scaffolding
# ---------------------------------------------------------------------------

_SYSTEM = (
    "你是 ModelBridge 的项目分析助手。\n"
    "你的任务是基于扫描结果生成一份高质量的 `AGENT.md`，"
    "供未来 AI Coding Agent 在这个项目里工作时遵守。\n\n"
    "输出严格使用以下结构 (二级标题原样保留)，"
    "缺失信息就写「未知」而不是编造：\n"
    "1. ## Project Overview — 这是个什么项目，做什么用\n"
    "2. ## Tech Stack — 主要语言 / 框架 / 包管理器 / 部署方式\n"
    "3. ## Common Commands — 安装 / 启动 / 测试 / 构建 (用 `bash 块)\n"
    "4. ## Directory Structure — 关键目录和文件作用\n"
    "5. ## Coding Rules — 代码规范、命名、提交\n"
    "6. ## Agent Instructions — AI 在这里工作时必须遵守的事 (重要)\n"
    "7. ## Safety Rules — 不读 .env、不删用户文件、修改前先 diff 等\n"
    "8. ## Known Notes — 已知坑、依赖版本、todo\n\n"
    "输出 **必须是合法的 Markdown**，最外层用一级 `# AGENT.md`。"
    "不要解释你自己在做什么；直接输出 AGENT.md 的最终内容。"
)


def build_prompt(summary: ProjectSummary) -> str:
    """The user-side prompt — handed to the model alongside ``_SYSTEM``."""
    return (
        "请基于以下扫描结果生成 AGENT.md。\n\n"
        "```\n" + summary.to_markdown() + "\n```\n\n"
        "记住：缺失信息就写「未知」；保留二级标题顺序；"
        "Common Commands 段务必给出可直接复制的 shell 命令。"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    summary: ProjectSummary
    model_used: str
    agent_md: str
    elapsed_ms: int
    target_path: Path
    overwrote: bool = False


def generate_agent_md(
    project_path: Path | str,
    *,
    model_name: str | None = None,
    timeout: float = 120.0,
) -> GenerationResult:
    """Run scan → model → return text. Does **not** write to disk.

    Raises :class:`modelbridge.providers.ProviderError` /
    :class:`modelbridge.client.ChatError` if the model call fails — the
    caller is expected to print a friendly diagnostic.
    """
    root = Path(project_path).expanduser().resolve()
    summary = scan_project(root)
    prompt = build_prompt(summary)

    entry, response = chat_once(
        prompt,
        model_name=model_name,
        system=_SYSTEM,
        timeout=timeout,
        verbose_label="project_init",
    )

    text = _post_process(response.content or "", summary)
    return GenerationResult(
        summary=summary,
        model_used=entry.name,
        agent_md=text,
        elapsed_ms=response.elapsed_ms,
        target_path=root / DEFAULT_TARGET_FILENAME,
    )


def write_agent_md(
    result: GenerationResult,
    *,
    force: bool = False,
) -> bool:
    """Persist ``result.agent_md`` to ``result.target_path``.

    Returns ``True`` if the file was written, ``False`` if we refused
    because the file already exists and ``force=False``. The caller is
    responsible for any interactive confirmation before calling this.
    """
    path = result.target_path
    if path.exists() and not force:
        return False
    overwrote = path.exists()
    path.write_text(result.agent_md, encoding="utf-8")
    result.overwrote = overwrote
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_process(text: str, summary: ProjectSummary) -> str:
    """Light cleanup: strip leading/trailing junk, ensure top-level heading."""
    t = text.strip()
    # Some models wrap the answer in ``` markdown blocks. Unwrap once.
    if t.startswith("```"):
        t = t.strip("`").lstrip("markdown").lstrip().rstrip()
        if t.endswith("```"):
            t = t[:-3].rstrip()
    if not t.lstrip().startswith("# "):
        t = f"# AGENT.md ({summary.project_name})\n\n" + t
    if not t.endswith("\n"):
        t += "\n"
    return t


__all__ = [
    "DEFAULT_TARGET_FILENAME",
    "GenerationResult",
    "generate_agent_md",
    "write_agent_md",
    "build_prompt",
]
