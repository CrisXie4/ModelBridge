"""Build chat ``messages`` with a **stable section order** for cache hits.

Why order matters
-----------------
Providers like DeepSeek, Qwen and Kimi cache the longest stable prefix of
``messages``. If we shuffle our system/rules/summary text between requests
(or inject a timestamp at the top), each request looks like a new prefix
and the cache never hits. So this module imposes one fixed order:

1. **core_system**     — ``~/.modelbridge/system.md`` (or a built-in default)
2. **global_rules**    — ``~/.modelbridge/rules.md``
3. **project_rules**   — merged AGENT.md / CLAUDE.md / .cursorrules / etc.
4. **project_summary** — output of ``project/scanner.py``
5. **tools_schema**    — placeholder section so future tool-call definitions
                         get a deterministic position (currently empty)
6. **history**         — prior conversation
7. **user_request**    — the current turn's user message

Sections 1–5 form the **stable prefix**: we hash them as
``prompt_prefix_hash`` so callers (and ``--verbose``) can verify the cache
key. Sections 6–7 are the variable tail.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..schemas import ChatMessage
from .defaults import DEFAULT_SYSTEM_MD
from .rules_loader import (
    discover_rule_files,
    load_system_prompt,
    merge_rules,
)

# Avoid an import cycle: project.file_reader -> project.scanner -> nothing,
# so importing FileContext lazily is fine.
from ..project.file_reader import FileContext, render_file_context


# Canonical section order — DO NOT REORDER without a major-version bump.
SECTION_ORDER: tuple[str, ...] = (
    "core_system",
    "global_rules",
    "project_rules",
    "project_summary",
    "project_files",
    "tools_schema",
    "history",
    "user_request",
)

# Sections that contribute to the cached prefix. These are the bytes a
# provider's prefix-cache will key on.
#
# NOTE: ``project_files`` is deliberately NOT in the prefix. The file
# selector picks different files per query, so its content changes every
# turn — leaving it in the prefix would invalidate the cache for every
# new question. Instead it is folded into the dynamic user message so
# the prefix can stay stable across queries within the same project.
PREFIX_SECTIONS: tuple[str, ...] = (
    "core_system",
    "global_rules",
    "project_rules",
    "project_summary",
    "tools_schema",
)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class PromptBuildResult:
    """Everything callers need to send the request *and* render diagnostics."""

    messages: list[ChatMessage]
    sections: dict[str, str]
    sources: dict[str, list[str]] = field(default_factory=dict)
    total_chars: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    # Stable-prefix hashes (8-char hex, fnv-style truncation of sha256).
    rules_hash: str = ""
    project_summary_hash: str = ""
    prompt_prefix_hash: str = ""
    # Per-section 8-char hashes — used by ``mbridge prompt diff`` to point
    # at the exact section that drifted. Always populated, even for empty
    # sections (so absence is itself observable).
    section_hashes: dict[str, str] = field(default_factory=dict)
    # Derived hashes called out in the cache-hit-rate spec. ``file_tree_hash``
    # is sourced from the ProjectSummary (the full uncapped path list);
    # ``selected_files_hash`` covers the project_files section content;
    # ``dynamic_suffix_hash`` covers the per-turn tail (history +
    # user_request) so callers can confirm only the suffix changed.
    file_tree_hash: str = ""
    selected_files_hash: str = ""
    dynamic_suffix_hash: str = ""
    # Alias retained because the spec / verify-script ask for it by name.
    stable_prefix_hash: str = ""

    def section_summary(self) -> list[tuple[str, int, str]]:
        """``(name, chars, first-line-preview)`` for ``mbridge prompt show`` / ``/prompt``."""
        rows: list[tuple[str, int, str]] = []
        for name in SECTION_ORDER:
            body = self.sections.get(name, "")
            if not body:
                continue
            head = body.strip().splitlines()[0][:80] if body.strip() else ""
            rows.append((name, len(body), head))
        return rows


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

@dataclass
class PromptBuilder:
    """Compose the canonical messages list for a single request."""

    # Mutables set by ``with_*`` methods; final assembly happens in ``build()``.
    project_path: Path | None = None
    project_summary_text: str | None = None
    project_summary_file_tree_hash: str = ""
    project_files: list[FileContext] = field(default_factory=list)
    use_project_rules: bool = True
    use_global_rules: bool = True
    use_system_prompt: bool = True
    system_prompt_override: str | None = None
    history: list[ChatMessage] = field(default_factory=list)
    user_request: str | None = None
    tools_schema_text: str = ""  # reserved for v0.5+ — placeholder for now
    # Internal counter: how many role=="system" messages were dropped from
    # the last with_history() call. Surfaced as a warning in build().
    _history_system_dropped: int = field(default=0, repr=False)

    # ------------------------------------------------------------------
    # Fluent builders (each returns self so you can chain).
    # ------------------------------------------------------------------

    def with_project(self, path: Path | str | None) -> "PromptBuilder":
        if path is None:
            self.project_path = None
            return self
        p = Path(path).expanduser().resolve()
        self.project_path = p if p.exists() else None
        return self

    def with_project_summary(
        self,
        text: str | None,
        *,
        file_tree_hash: str = "",
    ) -> "PromptBuilder":
        self.project_summary_text = (text or "").strip() or None
        # Threading file_tree_hash through here keeps the PromptBuilder
        # decoupled from ProjectSummary while still letting callers expose
        # the underlying tree hash in PromptBuildResult.
        self.project_summary_file_tree_hash = file_tree_hash or ""
        return self

    def with_project_files(self, files: list[FileContext]) -> "PromptBuilder":
        """Attach selected, already-read project files (phase 5)."""
        self.project_files = list(files or [])
        return self

    def with_history(self, history: list[ChatMessage]) -> "PromptBuilder":
        # Strip out any messages that are already system/tool we wouldn't
        # want twice — but keep user / assistant / tool turns intact.
        filtered = [m for m in history if m.role != "system"]
        self._history_system_dropped = len(history) - len(filtered)
        self.history = filtered
        return self

    def with_user_request(self, text: str | None) -> "PromptBuilder":
        self.user_request = text
        return self

    def with_tools_schema(self, schema_text: str) -> "PromptBuilder":
        self.tools_schema_text = schema_text
        return self

    def with_system_prompt(self, text: str | None) -> "PromptBuilder":
        """Override the ``core_system`` section text (skip on-disk lookup).

        Passing ``None`` reverts to the default behaviour (load ``system.md``
        from the app dir, or fall back to the built-in default).
        """
        self.system_prompt_override = text
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> PromptBuildResult:
        sections: dict[str, str] = {name: "" for name in SECTION_ORDER}
        sources: dict[str, list[str]] = {name: [] for name in SECTION_ORDER}
        warnings: list[str] = []
        truncated = False

        # Warn about history system messages that were silently dropped
        if self._history_system_dropped > 0:
            n = self._history_system_dropped
            warnings.append(f"history 中过滤掉 {n} 条 system 消息")

        # 1) core_system
        if self.use_system_prompt:
            if self.system_prompt_override is not None:
                text = self.system_prompt_override
                sources["core_system"].append("<caller override>")
            else:
                loaded = load_system_prompt()
                if loaded is None:
                    text = DEFAULT_SYSTEM_MD
                    sources["core_system"].append("<built-in default>")
                else:
                    text = loaded
                    sources["core_system"].append("~/.modelbridge/system.md")
            sections["core_system"] = text.strip() + "\n"

        # 2) global_rules
        if self.use_global_rules:
            user_files = [f for f in discover_rule_files(None) if f.scope == "user_global"]
            if user_files:
                merged = merge_rules(user_files)
                if merged.text:
                    sections["global_rules"] = merged.text
                    sources["global_rules"] = [str(f.path) for f in merged.files]
                    warnings.extend(merged.warnings)
                    truncated = truncated or merged.truncated

        # 3) project_rules
        if self.use_project_rules and self.project_path is not None:
            project_files = [
                f for f in discover_rule_files(self.project_path)
                if f.scope != "user_global"
            ]
            if project_files:
                merged = merge_rules(project_files)
                if merged.text:
                    sections["project_rules"] = merged.text
                    sources["project_rules"] = [str(f.path) for f in merged.files]
                    warnings.extend(merged.warnings)
                    truncated = truncated or merged.truncated

        # 4) project_summary
        if self.project_summary_text:
            sections["project_summary"] = (
                "# Project Summary\n\n" + self.project_summary_text.strip() + "\n"
            )
            sources["project_summary"].append(str(self.project_path or "<inline>"))

        # 4.5) project_files (phase 5)
        if self.project_files:
            blocks: list[str] = ["# Project Files (selected excerpts)\n"]
            file_sources: list[str] = []
            for fc in self.project_files:
                blocks.append(render_file_context(fc))
                if fc.skipped_reason:
                    file_sources.append(f"{fc.path} (skipped: {fc.skipped_reason})")
                else:
                    tag = "truncated" if fc.truncated else f"{fc.lines_read}L"
                    file_sources.append(f"{fc.path} ({tag})")
            sections["project_files"] = "\n".join(blocks)
            sources["project_files"] = file_sources

        # 5) tools_schema (placeholder)
        if self.tools_schema_text:
            sections["tools_schema"] = self.tools_schema_text.strip() + "\n"

        # 6) history (sections stores a summary; actual messages stay typed)
        history_msgs: list[ChatMessage] = list(self.history)
        if history_msgs:
            sections["history"] = f"<{len(history_msgs)} prior message(s)>"
            sources["history"].append(f"{len(history_msgs)} messages")

        # 7) user_request
        if self.user_request:
            sections["user_request"] = self.user_request

        # ------- assemble messages list ---------------------------------
        messages: list[ChatMessage] = []

        # Concatenate the stable prefix into ONE system message so the
        # provider sees a single block (cache-friendly). Skipping empty
        # sections keeps the prefix tight.
        prefix_parts: list[str] = []
        for name in PREFIX_SECTIONS:
            body = sections.get(name, "")
            if body:
                prefix_parts.append(body.rstrip())
        if prefix_parts:
            messages.append(ChatMessage(role="system", content="\n\n".join(prefix_parts)))

        # History (already typed)
        messages.extend(history_msgs)

        # Final user message: ``project_files`` rides along inside this
        # message rather than the stable prefix, so the prefix stays
        # stable across queries (file selector picks different files per
        # query). Concretely the message body looks like::
        #
        #     # Project Files (selected excerpts)
        #     ...file_a...
        #     ...file_b...
        #
        #     <the user's actual question>
        #
        # Provider prefix-caches still hit on the leading system message
        # plus any unchanged history; only this tail is fresh per turn.
        if self.user_request is not None and self.user_request.strip():
            files_block = sections.get("project_files", "").strip()
            if files_block:
                user_body = f"{files_block}\n\n{self.user_request}"
            else:
                user_body = self.user_request
            messages.append(ChatMessage(role="user", content=user_body))

        # ------- hashes -------------------------------------------------
        rules_blob = sections.get("global_rules", "") + sections.get("project_rules", "")
        summary_blob = sections.get("project_summary", "")
        prefix_blob = "\n\n".join(
            s for name in PREFIX_SECTIONS if (s := sections.get(name, ""))
        )
        # Dynamic tail — everything that's allowed to change per request.
        suffix_blob = (
            sections.get("history", "")
            + "\n\n"
            + sections.get("user_request", "")
        )

        rules_hash = _short_hash(rules_blob)
        project_summary_hash = _short_hash(summary_blob)
        prompt_prefix_hash = _short_hash(prefix_blob)
        dynamic_suffix_hash = _short_hash(suffix_blob)
        selected_files_hash = _short_hash(sections.get("project_files", ""))
        # ``file_tree_hash`` reflects the *underlying* tree (uncapped, sorted)
        # as computed by ProjectSummary. We don't recompute it from the
        # rendered summary text because the summary truncates the tree at
        # MAX_TREE_ENTRIES — that would make the hash useless for cache
        # invalidation of large projects.
        file_tree_hash = self.project_summary_file_tree_hash or _short_hash(
            sections.get("project_summary", "")
        )

        section_hashes = {
            name: _short_hash(sections.get(name, ""))
            for name in SECTION_ORDER
        }

        total_chars = sum(len(v) for v in sections.values())

        return PromptBuildResult(
            messages=messages,
            sections=sections,
            sources=sources,
            total_chars=total_chars,
            truncated=truncated,
            warnings=warnings,
            rules_hash=rules_hash,
            project_summary_hash=project_summary_hash,
            prompt_prefix_hash=prompt_prefix_hash,
            stable_prefix_hash=prompt_prefix_hash,  # alias — same value
            section_hashes=section_hashes,
            file_tree_hash=file_tree_hash,
            selected_files_hash=selected_files_hash,
            dynamic_suffix_hash=dynamic_suffix_hash,
        )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _short_hash(text: str) -> str:
    if not text:
        return "0" * 8
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return h[:8]


__all__ = [
    "SECTION_ORDER",
    "PREFIX_SECTIONS",
    "PromptBuildResult",
    "PromptBuilder",
]
