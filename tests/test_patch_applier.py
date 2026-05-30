"""Unit tests for the context-anchored patch applier.

Drives ``apply_diff`` against real files in a ``tmp_path`` sandbox: plain
modifies, fuzzy anchor matching when the hunk line number is off, file
create/delete sentinels, conflict refusal (no half-applied writes), and
``dry_run`` (match but don't write).
"""

from __future__ import annotations

from modelbridge.editor.diff_parser import parse_unified_diff
from modelbridge.editor.patch_applier import apply_diff


def _apply(text, tmp_path, *, dry_run=False):
    return apply_diff(parse_unified_diff(text), project_root=tmp_path, dry_run=dry_run)


MODIFY = """\
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 a = 1
-b = 2
+b = 20
 c = 3
"""


def test_modify_applies(tmp_path):
    (tmp_path / "foo.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    res = _apply(MODIFY, tmp_path)
    assert res.all_ok
    assert res.modified == ["foo.py"]
    assert (tmp_path / "foo.py").read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"


def test_modify_with_wrong_line_number_still_anchors(tmp_path):
    # Hunk claims old_start=1, but the matching context sits two lines down.
    # The fuzzy anchor search (±FUZZ_LINES) should still find it.
    (tmp_path / "foo.py").write_text(
        "# header\n# header2\na = 1\nb = 2\nc = 3\n", encoding="utf-8"
    )
    res = _apply(MODIFY, tmp_path)
    assert res.all_ok
    assert (tmp_path / "foo.py").read_text(encoding="utf-8") == (
        "# header\n# header2\na = 1\nb = 20\nc = 3\n"
    )


def test_create_new_file(tmp_path):
    diff = "--- /dev/null\n+++ b/sub/new.py\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
    res = _apply(diff, tmp_path)
    assert res.all_ok
    assert res.created == ["sub/new.py"]
    assert (tmp_path / "sub" / "new.py").read_text(encoding="utf-8") == "hello\nworld\n"


def test_create_fails_when_target_exists(tmp_path):
    (tmp_path / "exists.py").write_text("x\n", encoding="utf-8")
    diff = "--- /dev/null\n+++ b/exists.py\n@@ -0,0 +1,1 @@\n+y\n"
    res = _apply(diff, tmp_path)
    assert res.any_failure
    assert res.files[0].status == "failed"
    # Existing file is left untouched.
    assert (tmp_path / "exists.py").read_text(encoding="utf-8") == "x\n"


def test_delete_file(tmp_path):
    (tmp_path / "old.py").write_text("x\ny\n", encoding="utf-8")
    diff = "--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x\n-y\n"
    res = _apply(diff, tmp_path)
    assert res.all_ok
    assert res.deleted == ["old.py"]
    assert not (tmp_path / "old.py").exists()


def test_conflict_refuses_and_leaves_file_untouched(tmp_path):
    (tmp_path / "f.py").write_text("a = 1\n", encoding="utf-8")
    diff = (
        "--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n a = 1\n"
        "-totally not here\n+replacement\n"
    )
    res = _apply(diff, tmp_path)
    assert res.any_failure
    assert res.files[0].status == "failed"
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a = 1\n"


def test_dry_run_matches_but_does_not_write(tmp_path):
    (tmp_path / "foo.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    res = _apply(MODIFY, tmp_path, dry_run=True)
    assert res.all_ok
    assert res.dry_run
    # File on disk is unchanged.
    assert (tmp_path / "foo.py").read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
