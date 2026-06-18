"""Tests that empty sections do not affect the stable prefix hash.

Bug: prefix_blob was computed by joining ALL PREFIX_SECTIONS with "\\n\\n",
including empty ones. This means the hash includes extra "\\n\\n" separators
for every empty section slot — making the hash different from a semantically
equivalent build that omits those empty sections.

The symptom: two builds with identical *non-empty* content produce different
hashes solely because one has more empty section slots, breaking cache
stability across situations where sections toggle between populated/empty.

Fix: filter out empty sections before joining prefix_blob so the hash only
reflects actual content.
"""

import hashlib

from modelbridge.prompt.builder import PromptBuilder, PREFIX_SECTIONS


def _short_hash(text: str) -> str:
    """Replicate the builder's internal hash function for comparison."""
    if not text:
        return "0" * 8
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return h[:8]


def _make() -> PromptBuilder:
    """Hermetic builder: disable on-disk rule loading so tests don't depend
    on the user's global rules.md file."""
    b = PromptBuilder()
    b.use_global_rules = False
    b.use_project_rules = False
    return b


def test_prefix_hash_equals_fixed_blob_not_buggy_blob() -> None:
    """Core regression test: the stored hash must equal the hash of the
    empty-filtered prefix_blob, NOT the naive join (which includes empty
    section separators).

    With global_rules/project_rules/tools_schema all empty, the naive join
    produces 'core\\n\\n\\n\\nproject_summary\\n\\n' — the fixed join
    produces 'core\\n\\nproject_summary'.

    Before the fix: stable_prefix_hash == buggy_hash (FAIL — mismatch with fixed)
    After the fix:  stable_prefix_hash == fixed_hash (PASS)
    """
    result = (
        _make()
        .with_system_prompt("Core text")
        .with_project_summary("Summary text")
        .build()
    )

    # Compute both variants manually from the result's section dict
    buggy_blob = "\n\n".join(
        result.sections.get(name, "") for name in PREFIX_SECTIONS
    )
    fixed_blob = "\n\n".join(
        s for name in PREFIX_SECTIONS
        if (s := result.sections.get(name, ""))
    )

    # Pre-condition: there must be empty sections for the bug to manifest
    assert len(buggy_blob) > len(fixed_blob), (
        "Pre-condition: there should be empty PREFIX_SECTIONS to trigger the bug. "
        "Check that global_rules / project_rules / tools_schema are all empty "
        "in this build."
    )

    fixed_hash = _short_hash(fixed_blob)
    buggy_hash = _short_hash(buggy_blob)

    # The stored hash must match the FIXED blob
    assert result.stable_prefix_hash == fixed_hash, (
        f"stable_prefix_hash={result.stable_prefix_hash!r} should equal "
        f"fixed_hash={fixed_hash!r} (empty-filtered join), "
        f"not buggy_hash={buggy_hash!r} (naive join with empty-section separators). "
        "The fix must filter empty sections before joining prefix_blob."
    )


def test_prefix_hash_same_content_different_empty_sections() -> None:
    """Two builds with identical non-empty sections must yield the same hash,
    regardless of which empty sections are present.

    Scenario: build_a has core+summary only; build_b has core+summary and
    an additional empty tools_schema set explicitly. Before the fix they
    both use the same default (tools_schema="") but the hash key must not
    distinguish between 'tools_schema set to empty explicitly' vs 'never set'.

    Actually: since both builds always include all PREFIX_SECTIONS in the
    join, the distinction here is: same content → same hash (idempotency).
    This test verifies the hash is stable for identical content.
    """
    result_a = (
        _make()
        .with_system_prompt("Stable core")
        .with_project_summary("Stable summary")
        .build()
    )
    result_b = (
        _make()
        .with_system_prompt("Stable core")
        .with_project_summary("Stable summary")
        .with_tools_schema("")  # explicitly empty — same semantic content as a
        .build()
    )

    assert result_a.stable_prefix_hash == result_b.stable_prefix_hash, (
        "Two builds with identical content must produce the same "
        "stable_prefix_hash regardless of how empty sections were set."
    )


def test_prefix_hash_changes_when_content_added() -> None:
    """Sanity check: the hash MUST change when real content is added."""
    result_no_schema = (
        _make()
        .with_system_prompt("Core")
        .with_project_summary("Summary")
        .build()
    )
    result_with_schema = (
        _make()
        .with_system_prompt("Core")
        .with_project_summary("Summary")
        .with_tools_schema("schema content")
        .build()
    )

    assert result_no_schema.stable_prefix_hash != result_with_schema.stable_prefix_hash, (
        "Adding tools_schema content must change stable_prefix_hash."
    )


def test_prefix_hash_stable_prefix_hash_alias() -> None:
    """stable_prefix_hash and prompt_prefix_hash must be the same value
    (alias retained for backward compatibility)."""
    result = (
        _make()
        .with_system_prompt("Core")
        .build()
    )
    assert result.stable_prefix_hash == result.prompt_prefix_hash, (
        "stable_prefix_hash must be an alias for prompt_prefix_hash."
    )
