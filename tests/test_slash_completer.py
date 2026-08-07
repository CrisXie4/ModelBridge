"""Headless tests for the prompt_toolkit /slash-command completer."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from modelbridge.agent.slash_completer import SlashCommandCompleter


def _display_text(comp) -> str:
    """Extract plain text from a Completion's display (FormattedText or str)."""
    d = comp.display
    if isinstance(d, str):
        return d
    return "".join(seg[1] for seg in d)


def _complete(c: SlashCommandCompleter, text: str) -> list:
    doc = Document(text, len(text))
    return list(c.get_completions(doc, CompleteEvent()))


def test_bare_slash_offers_all_commands():
    c = SlashCommandCompleter()
    comps = _complete(c, "/")
    names = {comp.text for comp in comps}
    # Core commands must show up.
    assert "help" in names
    assert "tokens" in names
    assert "exit" in names
    # Every offered completion is displayed with a leading slash.
    assert all(_display_text(comp).startswith("/") for comp in comps)


def test_prefix_filters():
    c = SlashCommandCompleter()
    comps = _complete(c, "/to")
    names = {comp.text for comp in comps}
    # /to should match tokens, token, t, tools — but NOT exit/help.
    assert "tokens" in names
    assert "tools" in names
    assert "exit" not in names
    assert "help" not in names


def test_start_position_replaces_partial():
    c = SlashCommandCompleter()
    comps = _complete(c, "/tok")
    assert comps
    # The completion must replace the partial "tok" (start_position = -3).
    assert all(comp.start_position == -3 for comp in comps)


def test_no_slash_no_completions():
    c = SlashCommandCompleter()
    # Plain text without a leading slash should not trigger command completion.
    assert _complete(c, "hello") == []
    assert _complete(c, "tokens") == []


def test_stops_after_space_args_not_completed():
    c = SlashCommandCompleter()
    # Once a space is typed, we're in argument territory — no more
    # command-name suggestions (we deliberately don't complete args).
    assert _complete(c, "/think ") == []
    assert _complete(c, "/tokens off") == []


def test_completions_carry_descriptions():
    c = SlashCommandCompleter()
    comps = {comp.text: comp.display_meta for comp in _complete(c, "/")}
    # /tokens should carry a non-empty description from the help table.
    assert comps.get("tokens")


def test_multiline_does_not_fire_on_mid_text_slash():
    c = SlashCommandCompleter()
    # A "/" not at the start of its line should not trigger completion.
    assert _complete(c, "some text /tokens") == []
    # A fresh line starting with "/" does.
    comps = _complete(c, "previous line\n/t")
    assert comps and any(comp.text == "tokens" for comp in comps)
