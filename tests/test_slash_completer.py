"""Headless tests for the slash-command completer (command name + subcommands)."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from modelbridge.agent.slash_completer import SlashCommandCompleter


def _complete(c: SlashCommandCompleter, text: str) -> list:
    doc = Document(text, len(text))
    return list(c.get_completions(doc, CompleteEvent()))


def _texts(c: SlashCommandCompleter, text: str) -> list[str]:
    return [comp.text for comp in _complete(c, text)]


def test_command_name_completion() -> None:
    c = SlashCommandCompleter()
    assert "help" in _texts(c, "/h")
    assert "think" in _texts(c, "/t")


def test_command_name_completion_no_prefix_yields_all() -> None:
    c = SlashCommandCompleter()
    texts = _texts(c, "/")
    # a handful of known commands must show up
    for name in ("help", "model", "think", "mcp"):
        assert name in texts


def test_subcommand_completion_think_on() -> None:
    c = SlashCommandCompleter()
    texts = _texts(c, "/think o")
    assert "on" in texts
    assert "off" in texts


def test_subcommand_completion_mcp_list() -> None:
    c = SlashCommandCompleter()
    texts = _texts(c, "/mcp l")
    assert "list" in texts


def test_subcommand_completion_no_prefix_yields_all_subs() -> None:
    c = SlashCommandCompleter()
    texts = _texts(c, "/mcp ")
    assert "list" in texts
    assert "tools" in texts
    assert "refresh" in texts


def test_subcommand_completion_unknown_command_offers_nothing() -> None:
    c = SlashCommandCompleter()
    assert _texts(c, "/nonexistent x") == []


def test_subcommand_completion_stops_after_second_token() -> None:
    # /think on <more> — we only complete the first argument.
    c = SlashCommandCompleter()
    assert _texts(c, "/think on more") == []


def test_model_subcommand_uses_provider() -> None:
    c = SlashCommandCompleter(model_names_provider=lambda: ["deepseek-chat", "qwen-turbo"])
    texts = _texts(c, "/model deep")
    assert "deepseek-chat" in texts
    assert "qwen-turbo" not in texts


def test_model_subcommand_no_provider_offers_nothing() -> None:
    c = SlashCommandCompleter()
    assert _texts(c, "/model deep") == []


def test_model_subcommand_provider_error_swallows() -> None:
    def boom() -> list[str]:
        raise RuntimeError("nope")
    c = SlashCommandCompleter(model_names_provider=boom)
    assert _texts(c, "/model ") == []


def test_multiline_does_not_fire_mid_line() -> None:
    c = SlashCommandCompleter()
    # A non-slash line offers nothing.
    assert _texts(c, "hello world") == []
    # /th on its own line (after a newline) still completes — the completer
    # only looks at the current line.
    texts = _texts(c, "print()\n/th")
    assert "think" in texts


def test_non_slash_line_offers_nothing_repeated() -> None:
    # sanity: a plain question never offers slash completions
    c = SlashCommandCompleter()
    assert _texts(c, "how do I parse json?") == []


def test_init_flags() -> None:
    c = SlashCommandCompleter()
    texts = _texts(c, "/init --")
    assert "--force" in texts
    assert "--yes" in texts


def test_debug_on_off() -> None:
    c = SlashCommandCompleter()
    texts = _texts(c, "/debug o")
    assert "on" in texts
    assert "off" in texts
