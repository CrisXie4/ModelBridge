"""prompt_toolkit completer for ``/slash`` commands.

Complements :class:`~modelbridge.agent.at_completer.AtFileCompleter` (which
handles ``@file`` mentions). Offered only while the cursor is inside the
*command name* (the first whitespace-delimited token of the line, starting
with ``/``). Once the user types a space we stop suggesting — slash-command
arguments (model names, level numbers, …) are intentionally not
auto-completed, since they're short and usually free-form.

The command table is pulled lazily from
:func:`modelbridge.agent.commands.slash_command_help` so the completer
always reflects the live registry (no second list to keep in sync).
"""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from .commands import slash_command_help


class SlashCommandCompleter(Completer):
    """Offer ``/command`` completions for the first token of a line."""

    def __init__(self) -> None:
        self._help = slash_command_help()

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        # Only complete a leading slash command. We look at the line the
        # cursor is on so a ``/`` mid-multiline input doesn't fire.
        line_up_to_cursor = text.rsplit("\n", 1)[-1]
        if not line_up_to_cursor.startswith("/"):
            return
        # Stop once the command name is finished (space entered) — we don't
        # autocomplete command arguments.
        name_part = line_up_to_cursor[1:]  # strip the leading "/"
        if " " in name_part:
            return
        name_part = name_part.lstrip()  # tolerate "//" double-slash typos
        for cmd, desc in self._help.items():
            if not name_part or cmd.startswith(name_part):
                yield Completion(
                    text=cmd,
                    start_position=-len(name_part),
                    display="/" + cmd,
                    display_meta=desc[:40] if desc else "",
                )


__all__ = ["SlashCommandCompleter"]
