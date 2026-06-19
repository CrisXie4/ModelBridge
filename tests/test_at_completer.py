"""Headless tests for the prompt_toolkit @-file completer."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from modelbridge.agent.at_completer import AtFileCompleter
from modelbridge.project.file_index import FileIndex


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    _touch(tmp_path / "main.py")
    _touch(tmp_path / "src" / "app1234.py")
    _touch(tmp_path / "src" / "utils.py")
    _touch(tmp_path / "README.md")
    return tmp_path


def _complete(completer: AtFileCompleter, text: str) -> list:
    doc = Document(text, len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def test_completer_suggests_matching_files(tree: Path) -> None:
    idx = FileIndex.build(tree)
    c = AtFileCompleter(lambda: idx)
    texts = [comp.text for comp in _complete(c, "看 @app1234")]
    assert "src/app1234.py" in texts


def test_completer_replaces_only_the_partial(tree: Path) -> None:
    idx = FileIndex.build(tree)
    c = AtFileCompleter(lambda: idx)
    comps = _complete(c, "@app1234")
    assert comps and all(comp.start_position == -len("app1234") for comp in comps)


def test_completer_bare_at_lists_some(tree: Path) -> None:
    idx = FileIndex.build(tree)
    c = AtFileCompleter(lambda: idx)
    assert len(_complete(c, "@")) > 0


def test_completer_directory_gets_trailing_slash(tree: Path) -> None:
    idx = FileIndex.build(tree)
    c = AtFileCompleter(lambda: idx)
    texts = [comp.text for comp in _complete(c, "@src")]
    assert "src/" in texts


def test_completer_no_mention_means_no_suggestions(tree: Path) -> None:
    idx = FileIndex.build(tree)
    c = AtFileCompleter(lambda: idx)
    assert _complete(c, "普通文本没有提及") == []


def test_completer_tolerates_missing_index() -> None:
    c = AtFileCompleter(lambda: None)
    assert _complete(c, "@app") == []
