"""Tests for the @-mention file index (FileIndex)."""

from __future__ import annotations

from pathlib import Path

import pytest

from modelbridge.project.file_index import FileEntry, FileIndex


def _touch(p: Path, content: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    _touch(tmp_path / "README.md")
    _touch(tmp_path / "main.py")
    _touch(tmp_path / "src" / "app1234.py")
    _touch(tmp_path / "src" / "utils.py")
    _touch(tmp_path / "data1234" / "rows.csv")          # dir name contains 1234
    # things that must be excluded
    _touch(tmp_path / "node_modules" / "left-pad" / "index.js")
    _touch(tmp_path / ".git" / "config")
    _touch(tmp_path / "__pycache__" / "x.pyc")
    _touch(tmp_path / ".env", "SECRET=1")
    _touch(tmp_path / "server.pem", "KEY")
    _touch(tmp_path / ".modelbridge" / "config.yaml", "k: v")
    _touch(tmp_path / ".modelbridge" / "cache" / "summary.json", "{}")
    return tmp_path


def _paths(entries: list[FileEntry]) -> set[str]:
    return {e.relpath for e in entries}


def test_build_collects_files_and_dirs(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    paths = _paths(idx.entries)
    assert "README.md" in paths
    assert "main.py" in paths
    assert "src/app1234.py" in paths
    assert "src" in paths            # directory entry present
    assert "data1234" in paths       # directory entry present


def test_build_excludes_skip_dirs(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    paths = _paths(idx.entries)
    assert not any(p.startswith("node_modules") for p in paths)
    assert not any(p.startswith(".git") for p in paths)
    assert not any("__pycache__" in p for p in paths)


def test_build_excludes_sensitive_files(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    paths = _paths(idx.entries)
    assert ".env" not in paths
    assert "server.pem" not in paths


def test_build_excludes_modelbridge_internal(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    paths = _paths(idx.entries)
    # .modelbridge/config.yaml is fine to mention, but the internal cache/ is not.
    assert ".modelbridge/config.yaml" in paths
    assert not any("/cache/" in p or p.endswith("/cache") for p in paths)


def test_build_excludes_extra_credential_files(tmp_path: Path) -> None:
    creds = (".npmrc", ".netrc", ".pgpass", ".htpasswd", "kubeconfig", ".dockercfg", ".pypirc")
    for name in creds:
        _touch(tmp_path / name)
    _touch(tmp_path / "app.py")  # a normal file stays
    idx = FileIndex.build(tmp_path)
    names = {e.relpath for e in idx.entries}
    for name in creds:
        assert name not in names, f"{name} should be filtered as sensitive"
    assert "app.py" in names


def test_entry_is_dir_flag(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    by_path = {e.relpath: e for e in idx.entries}
    assert by_path["src"].is_dir is True
    assert by_path["main.py"].is_dir is False


def test_match_empty_returns_some(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    res = idx.match("", limit=3)
    assert 0 < len(res) <= 3


def test_match_substring_filters(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    res = idx.match("1234")
    paths = _paths(res)
    assert "src/app1234.py" in paths
    assert "data1234" in paths       # folder also matches
    assert "main.py" not in paths    # no 1234 -> excluded


def test_match_case_insensitive(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    res = idx.match("readme")
    assert "README.md" in _paths(res)


def test_match_ranks_basename_hit_above_path_only_hit(sample_tree: Path) -> None:
    _touch(sample_tree / "alpha" / "zzz.py")        # 'alpha' only in the path
    _touch(sample_tree / "beta" / "alpha_core.py")  # 'alpha' in the basename
    idx = FileIndex.build(sample_tree)
    paths = [e.relpath for e in idx.match("alpha")]
    # the basename hit must rank before the path-only hit
    assert paths.index("beta/alpha_core.py") < paths.index("alpha/zzz.py")


def test_match_respects_limit(sample_tree: Path) -> None:
    idx = FileIndex.build(sample_tree)
    res = idx.match("", limit=2)
    assert len(res) == 2


def test_match_path_query_skips_dir_segments(tmp_path: Path) -> None:
    # Typing '@pkg/at' while drilling in should fuzzy-match across the
    # intervening 'agent/' segment, not require a literal substring.
    _touch(tmp_path / "pkg" / "agent" / "at_completer.py")
    _touch(tmp_path / "pkg" / "docs" / "readme.md")
    idx = FileIndex.build(tmp_path)
    paths = {e.relpath for e in idx.match("pkg/at")}
    assert "pkg/agent/at_completer.py" in paths
    assert "pkg/docs/readme.md" not in paths  # no 'a..t' subsequence after pkg/


def test_build_truncation_is_deterministic(tmp_path: Path) -> None:
    # Survivors must be the lexicographically-first max_entries, not whatever
    # os.walk happened to reach first.
    for i in range(8):
        _touch(tmp_path / f"dir{i}" / "f.txt")
    idx = FileIndex.build(tmp_path, max_entries=4)
    assert idx.truncated
    names = [e.relpath for e in idx.entries]
    assert names == sorted(names)
    assert names == ["dir0", "dir0/f.txt", "dir1", "dir1/f.txt"]


def test_match_normalizes_backslash_query(tmp_path: Path) -> None:
    # On Windows users naturally type '\'; the live completer must still match.
    _touch(tmp_path / "modelbridge" / "agent" / "x.py")
    idx = FileIndex.build(tmp_path)
    paths = {e.relpath for e in idx.match("modelbridge\\agent")}
    assert "modelbridge/agent/x.py" in paths


def test_build_skips_dot_directories_except_modelbridge(sample_tree: Path) -> None:
    _touch(sample_tree / ".github" / "workflows" / "ci.yml")
    _touch(sample_tree / ".aws" / "config")
    idx = FileIndex.build(sample_tree)
    paths = _paths(idx.entries)
    # generic dot-dirs (credentials/CI homes) are pruned, matching the scanner
    assert not any(p.startswith(".github") for p in paths)
    assert not any(p.startswith(".aws") for p in paths)
    # but the tool's own .modelbridge config stays mentionable
    assert ".modelbridge/config.yaml" in paths


def test_match_path_query_prefers_basename_segment_hit(tmp_path: Path) -> None:
    # Typing '@pkg/at', the file whose *basename* contains 'at' should win
    # over the shorter 'pkg/agent' dir that only matches via subsequence.
    _touch(tmp_path / "pkg" / "agent" / "at_completer.py")
    _touch(tmp_path / "pkg" / "agent" / "tools.py")
    idx = FileIndex.build(tmp_path)
    paths = [e.relpath for e in idx.match("pkg/at")]
    assert paths[0] == "pkg/agent/at_completer.py"


def test_match_no_slash_query_stays_substring(tmp_path: Path) -> None:
    # Without a slash we keep strict substring semantics ('含有') so short
    # queries stay clean: 'cli' must not subsequence-match 'css/lib/i.js'.
    _touch(tmp_path / "css" / "lib" / "i.js")
    _touch(tmp_path / "cli.py")
    idx = FileIndex.build(tmp_path)
    paths = {e.relpath for e in idx.match("cli")}
    assert "cli.py" in paths
    assert "css/lib/i.js" not in paths
