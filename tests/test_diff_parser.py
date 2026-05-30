"""Unit tests for the hand-rolled unified-diff parser.

Covers the public contract of ``modelbridge.editor.diff_parser``: normal
single/multi-file diffs, ``/dev/null`` create/delete sentinels, ``a/``/``b/``
prefix stripping, round-tripping, and the malformed-input paths that must
raise :class:`DiffParseError` rather than silently mis-parse.
"""

from __future__ import annotations

import pytest

from modelbridge.editor.diff_parser import (
    DiffParseError,
    parse_unified_diff,
    render_unified_diff,
)


SINGLE = """\
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2new
 line3
"""

MULTI = """\
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-old
+new
--- a/bar.py
+++ b/bar.py
@@ -1 +1 @@
-x
+y
"""

CREATE = """\
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+hello
+world
"""

DELETE = """\
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-hello
-world
"""


def test_single_file_diff():
    parsed = parse_unified_diff(SINGLE)
    assert len(parsed.files) == 1
    f = parsed.files[0]
    # a/ and b/ prefixes are stripped.
    assert f.old_path == "foo.py"
    assert f.new_path == "foo.py"
    assert f.effective_path == "foo.py"
    assert not f.is_creation and not f.is_deletion
    assert len(f.hunks) == 1
    h = f.hunks[0]
    # removed view = context + removed; added view = context + added.
    assert h.removed_lines == ["line1", "line2", "line3"]
    assert h.added_lines == ["line1", "line2new", "line3"]


def test_multi_file_diff():
    parsed = parse_unified_diff(MULTI)
    assert [f.effective_path for f in parsed.files] == ["foo.py", "bar.py"]
    assert all(len(f.hunks) == 1 for f in parsed.files)


def test_dev_null_creation():
    parsed = parse_unified_diff(CREATE)
    f = parsed.files[0]
    assert f.is_creation
    assert not f.is_deletion
    # For a creation the file the applier writes to is the new path.
    assert f.effective_path == "new.py"
    assert f.hunks[0].added_lines == ["hello", "world"]


def test_dev_null_deletion():
    parsed = parse_unified_diff(DELETE)
    f = parsed.files[0]
    assert f.is_deletion
    assert not f.is_creation
    # For a deletion the relevant path is the old one.
    assert f.effective_path == "old.py"


def test_round_trip_render_then_parse():
    parsed = parse_unified_diff(SINGLE)
    rendered = render_unified_diff(parsed)
    reparsed = parse_unified_diff(rendered)
    assert reparsed.files[0].effective_path == "foo.py"
    assert reparsed.files[0].hunks[0].added_lines == ["line1", "line2new", "line3"]


def test_git_preamble_is_tolerated():
    text = (
        "diff --git a/foo.py b/foo.py\n"
        "index 1234567..89abcde 100644\n" + SINGLE
    )
    parsed = parse_unified_diff(text)
    assert parsed.files[0].effective_path == "foo.py"


# --- malformed input must raise, never silently mis-parse -------------------

def test_empty_raises():
    with pytest.raises(DiffParseError):
        parse_unified_diff("")
    with pytest.raises(DiffParseError):
        parse_unified_diff("   \n  \n")


def test_no_headers_raises():
    with pytest.raises(DiffParseError):
        parse_unified_diff("just some prose\nwith no diff headers\n")


def test_minus_without_plus_raises():
    bad = "--- a/foo.py\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(DiffParseError):
        parse_unified_diff(bad)


def test_bad_hunk_header_raises():
    bad = "--- a/f.py\n+++ b/f.py\n@@ this is not valid @@\n x\n"
    with pytest.raises(DiffParseError):
        parse_unified_diff(bad)


def test_wildly_wrong_hunk_count_raises():
    # Header claims 10 old lines but only 2 appear (diff > 2) → reject.
    bad = "--- a/f.py\n+++ b/f.py\n@@ -1,10 +1,1 @@\n a\n-b\n"
    with pytest.raises(DiffParseError):
        parse_unified_diff(bad)
