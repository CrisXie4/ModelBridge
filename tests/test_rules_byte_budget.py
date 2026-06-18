"""Tests that merge_rules enforces max_rules_chars as a UTF-8 byte budget.

CJK characters are 3 bytes each in UTF-8.  Before the fix, the char-based
truncation would allow ~3x the configured byte budget to pass through.
"""

from pathlib import Path

from modelbridge.prompt.rules_loader import RuleFile, merge_rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rule_file(tmp_path: Path, content: str, label: str = "test_rules.md") -> RuleFile:
    p = tmp_path / label
    p.write_text(content, encoding="utf-8")
    return RuleFile(path=p, scope="project", label=label, size=p.stat().st_size)


# ---------------------------------------------------------------------------
# CJK byte-budget test
# ---------------------------------------------------------------------------

def test_cjk_truncation_stays_within_byte_budget(tmp_path: Path) -> None:
    """CJK content must be truncated so encoded output fits inside the budget
    (plus a small allowance for heading/marker overhead).

    Pre-fix this test FAILS because len() counts characters, so a 80-char
    budget lets ~240 bytes through (3 bytes per CJK char).
    """
    # "规则内容。" is 6 chars, each 3 bytes in UTF-8 → 18 bytes per repetition.
    cjk_content = "规则内容。" * 50   # 300 chars, 900 bytes of content
    rf = _make_rule_file(tmp_path, cjk_content)

    budget = 80
    result = merge_rules([rf], max_chars=budget)

    assert result.truncated is True, "Expected truncated=True for CJK content exceeding byte budget"

    encoded_len = len(result.text.encode("utf-8"))
    # Allow up to budget + 120 bytes for the heading and truncation marker.
    assert encoded_len <= budget + 120, (
        f"Encoded output is {encoded_len} bytes, expected <= {budget + 120}. "
        "The byte budget is not being respected (likely still counting chars)."
    )


# ---------------------------------------------------------------------------
# Pure-ASCII passthrough test
# ---------------------------------------------------------------------------

def test_ascii_rule_under_budget_is_not_truncated(tmp_path: Path) -> None:
    """Pure ASCII content well under the budget passes through untruncated."""
    ascii_content = "Follow PEP 8 style guidelines.\n" * 3   # 93 chars = 93 bytes
    rf = _make_rule_file(tmp_path, ascii_content)

    budget = 500
    result = merge_rules([rf], max_chars=budget)

    assert result.truncated is False, (
        "Pure-ASCII content well under budget should NOT be truncated"
    )
    assert result.text.strip() != "", "Expected non-empty merged text"
