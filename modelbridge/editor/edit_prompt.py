"""Build the prompt for ``mbridge edit`` and extract the diff from the response.

The phase-6 contract with the model is sharp:

> Output **one and only one** unified diff. No prose. No backticks (or if you
> use them, only ``` ```diff ``` ```). No full-file rewrites. No partial edits
> outside diff format.

We can't *force* models to obey, but we (a) give them a strict-format
system prompt, (b) provide an example, and (c) post-process the
response so harmless deviations (markdown fences, leading explanation
text) don't kill the pipeline.

The actual prompt assembly leans on the phase-5 :class:`PromptBuilder`
— same stable section order, same project rules, same file context —
plus an extra **edit_instructions** block we splice into the system
message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..context import plan as plan_context
from ..project import (
    FileContext,
    ProjectSummary,
    read_files,
    scan_project,
    select_files,
)
from ..prompt import PromptBuilder, PromptBuildResult
from ..schemas import ChatMessage


EDIT_SYSTEM_RULES = """\
你是 ModelBridge 的代码修改助手。你的唯一职责是输出一段**标准 unified diff**，描述对项目中已有文件的修改。

严格规则：
1. 你的回答**只能**是一段 unified diff，可以放在 ```diff ... ``` 代码块里。
2. 禁止输出整文件覆盖、伪代码、解释、注释、序号、Markdown 列表、JSON。
3. diff 必须包含完整的文件头：`--- a/path/to/file` 和 `+++ b/path/to/file`。
4. 每个 hunk 必须有合法的 `@@ -old,count +new,count @@` 头。
5. hunk 内每一行必须以 ` `（context）、`-`（删除）或 `+`（新增）开头。
6. 不要修改 `.env`、`.env.*`、`.ssh`、`id_rsa`、`id_ed25519`、`.git/`、`node_modules/`、`dist/`、`build/` 下的任何路径。
7. 如果用户的需求需要新建文件，使用 `--- /dev/null` + `+++ b/path/new_file`，hunk 只含 `+` 行。
8. 如果你判断需求超出"修改文件"范围（例如需要 shell、外部 API、人工决策），输出空 diff 并加一行注释 `# need-human-decision`。
9. 改动尽量小：只 patch 必要的行，保持周围 3-5 行 context 即可。

示例（你的回答应当**只有**这样的内容）：

```diff
--- a/src/auth.py
+++ b/src/auth.py
@@ -3,6 +3,7 @@ def login(username, password):
     if not username or not password:
         raise ValueError("missing credentials")
+    audit_log("login", username)
     return issue_token(username)
```
"""


# ---------------------------------------------------------------------------
# Build prompt
# ---------------------------------------------------------------------------

@dataclass
class EditPromptResult:
    """Everything CLI needs to send the request + diagnose later."""

    messages: list[ChatMessage]
    prompt_result: PromptBuildResult
    project_summary: ProjectSummary
    file_contexts: list[FileContext]
    """Files we read and pasted into the prompt (post-budget)."""
    selected_paths: list[str] = field(default_factory=list)


def build_edit_messages(
    user_request: str,
    *,
    project_root: Path | str,
    max_context: int | None = None,
) -> EditPromptResult:
    """Run scan → select → read → budget → PromptBuilder for ``edit``.

    The only difference vs ``chat --project`` is the extra
    :data:`EDIT_SYSTEM_RULES` block prepended via ``tools_schema``
    (we re-use that section slot since phase-6 has no tool schema yet).
    """
    root = Path(project_root).expanduser().resolve()
    summary = scan_project(root)
    selection = select_files(user_request, summary)
    files = read_files(selection.files, project_root=root)

    builder = (
        PromptBuilder()
        .with_user_request(user_request)
        .with_project(root)
        .with_project_summary(summary.to_markdown())
        .with_tools_schema(EDIT_SYSTEM_RULES)  # reuses the deterministic slot
    )

    preview = builder.build()
    rules_chars = (
        len(preview.sections.get("global_rules", ""))
        + len(preview.sections.get("project_rules", ""))
    )
    system_chars = len(preview.sections.get("core_system", ""))
    summary_chars = len(preview.sections.get("project_summary", ""))
    tools_chars = len(preview.sections.get("tools_schema", ""))

    if max_context is not None:
        plan = plan_context(
            files,
            user_query=user_request,
            rules_chars=rules_chars + tools_chars,  # tools_schema is part of the prefix
            system_chars=system_chars,
            project_summary_chars=summary_chars,
            max_chars=max_context,
        )
        files = plan.kept_files

    builder = builder.with_project_files(files)
    result = builder.build()

    return EditPromptResult(
        messages=result.messages,
        prompt_result=result,
        project_summary=summary,
        file_contexts=files,
        selected_paths=[sf.path for sf in selection.files],
    )


# ---------------------------------------------------------------------------
# Extract diff from model output
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_HEADER_LINE_RE = re.compile(r"^---\s+\S", re.MULTILINE)
_NEED_HUMAN_RE = re.compile(r"^\s*#?\s*need-human-decision\b", re.MULTILINE | re.IGNORECASE)


@dataclass
class ExtractedDiff:
    """Outcome of :func:`extract_diff`."""

    diff_text: str = ""
    needs_human: bool = False
    """True if the model emitted ``need-human-decision``."""
    extra_text: str = ""
    """Any prose the model wrote alongside the diff (logged, not used)."""


def extract_diff(model_output: str) -> ExtractedDiff:
    """Pull the unified diff out of a raw model response.

    Search order:
    1. ``need-human-decision`` short-circuit.
    2. The first ``` ```diff ``` ``` or ``` ```patch ``` ``` fenced block.
    3. A bare ``--- ...`` line followed by ``+++ ...`` somewhere in the text;
       take everything from the ``---`` onward until the response ends or a
       triple-backtick is hit.
    """
    if not model_output:
        return ExtractedDiff()

    if _NEED_HUMAN_RE.search(model_output) and not _HEADER_LINE_RE.search(model_output):
        return ExtractedDiff(needs_human=True, extra_text=model_output.strip())

    # 1) fenced block
    m = _FENCE_RE.search(model_output)
    if m:
        body = m.group(1)
        if _HEADER_LINE_RE.search(body):
            extra_before = model_output[: m.start()].strip()
            extra_after = model_output[m.end():].strip()
            extra = (extra_before + "\n\n" + extra_after).strip()
            return ExtractedDiff(diff_text=body.strip("\n") + "\n", extra_text=extra)

    # 2) bare diff anywhere in the text
    hdr = _HEADER_LINE_RE.search(model_output)
    if hdr:
        start = hdr.start()
        tail = model_output[start:]
        # If the tail starts with `--- ` but is followed by a fence later,
        # cut at the first fence.
        fence = tail.find("```")
        if fence > 0:
            tail = tail[:fence]
        extra = model_output[:start].strip()
        return ExtractedDiff(diff_text=tail.rstrip() + "\n", extra_text=extra)

    return ExtractedDiff(extra_text=model_output.strip())


__all__ = [
    "EDIT_SYSTEM_RULES",
    "EditPromptResult",
    "build_edit_messages",
    "ExtractedDiff",
    "extract_diff",
]
