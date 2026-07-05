"""prompt_toolkit completer for ``@file`` mentions.

This is the *only* module that imports prompt_toolkit; the CLI imports it
behind a ``try/except`` so a missing dependency or a non-TTY just falls
back to the plain reader. The completer is intentionally thin — all the
logic lives in :mod:`modelbridge.agent.mentions` and
:class:`~modelbridge.project.file_index.FileIndex`, which are pure and
unit-tested without a terminal.

The index is supplied via a zero-arg callable so the CLI can build it
lazily (on the first ``@``) and refresh it without rebuilding the
completer.
"""

from __future__ import annotations

from typing import Callable, Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from .mentions import mention_prefix_before_cursor
from ..project.file_index import DEFAULT_LIMIT, FileIndex


class AtFileCompleter(Completer):
    """Offer file/dir completions while the cursor is inside an ``@token``."""

    def __init__(
        self,
        index_provider: Callable[[], FileIndex | None],
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._provider = index_provider
        self._limit = limit

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        prefix = mention_prefix_before_cursor(document.text_before_cursor)
        if prefix is None:
            return
        try:
            index = self._provider()
        except Exception:
            return
        if index is None:
            return
        for entry in index.match(prefix, limit=self._limit):
            text = entry.relpath + "/" if entry.is_dir else entry.relpath
            yield Completion(
                text=text,
                start_position=-len(prefix),
                display=text,
                display_meta="dir" if entry.is_dir else "file",
            )


__all__ = ["AtFileCompleter"]
