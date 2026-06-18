"""Tests that system messages dropped from history produce a warning.

Bug: with_history() silently filters out role=="system" messages from the
history list. If persisted history contains system turns they vanish without
any indication in the result.

Fix: count the dropped system messages and append a warning to result.warnings
when N > 0 messages are filtered.

Expected warning format: f"history 中过滤掉 {n} 条 system 消息"
"""


from modelbridge.schemas import ChatMessage
from modelbridge.prompt.builder import PromptBuilder


def _make() -> PromptBuilder:
    """Hermetic builder — disables on-disk rule loading."""
    b = PromptBuilder()
    b.use_global_rules = False
    b.use_project_rules = False
    return b


def test_system_message_in_history_produces_warning() -> None:
    """A history list with a system message must produce a warning in
    result.warnings after build().

    This test is RED before the fix (no warning is emitted) and GREEN after.
    """
    history = [
        ChatMessage(role="system", content="injected system message"),
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi there"),
    ]

    result = (
        _make()
        .with_system_prompt("Core")
        .with_history(history)
        .with_user_request("new question")
        .build()
    )

    # The system message must NOT appear in the output messages
    system_in_output = [m for m in result.messages if m.role == "system" and "injected" in (m.content or "")]
    assert not system_in_output, (
        "Injected system message from history must be filtered out of messages."
    )

    # A warning MUST be present
    assert result.warnings, (
        "result.warnings should not be empty when a system message was dropped."
    )

    # At least one warning must mention the drop
    drop_warnings = [w for w in result.warnings if "system" in w and "1" in w]
    assert drop_warnings, (
        f"Expected a warning about 1 dropped system message, got: {result.warnings!r}"
    )


def test_system_message_warning_count_matches_dropped() -> None:
    """When multiple system messages are in history, the warning count
    must reflect the exact number dropped."""
    history = [
        ChatMessage(role="system", content="sys1"),
        ChatMessage(role="system", content="sys2"),
        ChatMessage(role="user", content="question"),
    ]

    result = (
        _make()
        .with_system_prompt("Core")
        .with_history(history)
        .build()
    )

    drop_warnings = [w for w in result.warnings if "system" in w and "2" in w]
    assert drop_warnings, (
        f"Expected a warning about 2 dropped system messages, got: {result.warnings!r}"
    )


def test_no_system_in_history_no_warning() -> None:
    """When history contains no system messages, no warning about system
    message dropping should appear."""
    history = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
    ]

    result = (
        _make()
        .with_system_prompt("Core")
        .with_history(history)
        .build()
    )

    system_drop_warnings = [
        w for w in result.warnings
        if "system" in w and ("过滤" in w or "filtered" in w or "system 消息" in w)
    ]
    assert not system_drop_warnings, (
        f"No warning should appear when no system messages are dropped. "
        f"Got: {result.warnings!r}"
    )


def test_non_system_history_messages_preserved() -> None:
    """user and assistant messages in history must not be dropped."""
    history = [
        ChatMessage(role="user", content="first"),
        ChatMessage(role="assistant", content="second"),
        ChatMessage(role="system", content="unwanted"),
    ]

    result = (
        _make()
        .with_system_prompt("Core")
        .with_history(history)
        .with_user_request("third")
        .build()
    )

    contents = [m.content for m in result.messages]
    assert "first" in contents, "user message 'first' must be preserved"
    assert "second" in contents, "assistant message 'second' must be preserved"
    assert "unwanted" not in contents, "system history message must be dropped"


def test_warning_text_format() -> None:
    """The warning text must match the specified format pattern for tooling
    that parses it: 'history 中过滤掉 N 条 system 消息'."""
    history = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="hi"),
    ]

    result = (
        _make()
        .with_system_prompt("Core")
        .with_history(history)
        .build()
    )

    matching = [
        w for w in result.warnings
        if "history" in w and "过滤掉" in w and "system 消息" in w
    ]
    assert matching, (
        f"Warning must contain 'history', '过滤掉', and 'system 消息'. "
        f"Got warnings: {result.warnings!r}"
    )
