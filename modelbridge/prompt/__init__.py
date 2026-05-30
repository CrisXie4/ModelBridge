"""Prompt assembly + rule-file loading.

Public surface:

* :class:`PromptBuilder` / :class:`PromptBuildResult` (``builder``)
* :func:`discover_rule_files` / :func:`load_global_rules` /
  :func:`load_project_rules` / :func:`load_system_prompt` (``rules_loader``)
* :data:`DEFAULT_SYSTEM_MD` / :data:`DEFAULT_RULES_MD` (``defaults``)
"""

from .builder import (
    PREFIX_SECTIONS,
    SECTION_ORDER,
    PromptBuildResult,
    PromptBuilder,
)
from .defaults import DEFAULT_RULES_MD, DEFAULT_SYSTEM_MD
from .rules_loader import (
    NESTED_RULE_FILES,
    PROJECT_RULE_FILES,
    MergedRules,
    RuleFile,
    discover_rule_files,
    load_global_rules,
    load_project_rules,
    load_system_prompt,
    merge_rules,
)

__all__ = [
    "DEFAULT_SYSTEM_MD",
    "DEFAULT_RULES_MD",
    "PROJECT_RULE_FILES",
    "NESTED_RULE_FILES",
    "RuleFile",
    "MergedRules",
    "discover_rule_files",
    "load_global_rules",
    "load_project_rules",
    "load_system_prompt",
    "merge_rules",
    "PromptBuilder",
    "PromptBuildResult",
    "SECTION_ORDER",
    "PREFIX_SECTIONS",
]
