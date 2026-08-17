"""Headless tests for the AI inline completer (AIAutoSuggest).

We don't hit the network — a fake ``chat_fn`` stands in for
:func:`modelbridge.client.chat_once`. The pure helpers (_build_prompt,
_extract_suffix) and the skip / debounce / incremental logic are the bits
worth locking down.
"""

from __future__ import annotations

import types

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from modelbridge.agent.ai_completer import (
    AIAutoSuggest,
    _build_prompt,
    _extract_suffix,
)
from modelbridge.schemas import ChatMessage


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_build_prompt_with_history() -> None:
    history = [
        ChatMessage(role="user", content="怎么解析 JSON"),
        ChatMessage(role="assistant", content="用 json.loads"),
    ]
    p = _build_prompt("import jso", history)
    assert "怎么解析 JSON" in p
    assert "用 json.loads" in p
    assert "import jso" in p


def test_build_prompt_no_history() -> None:
    p = _build_prompt("hello", [])
    assert "hello" in p
    assert "最近对话" not in p  # no transcript section when history is empty


def test_extract_suffix_plain() -> None:
    resp = types.SimpleNamespace(content="n.loads")
    assert _extract_suffix("import jso", resp) == "n.loads"


def test_extract_suffix_strips_quotes() -> None:
    resp = types.SimpleNamespace(content="`n.loads`")
    assert _extract_suffix("import jso", resp) == "n.loads"


def test_extract_suffix_strips_echoed_input() -> None:
    resp = types.SimpleNamespace(content="import json.loads")
    assert _extract_suffix("import jso", resp) == "n.loads"


def test_extract_suffix_collapses_newlines() -> None:
    resp = types.SimpleNamespace(content="n.loads\n\nimport sys")
    out = _extract_suffix("import jso", resp)
    assert "\n" not in out


def test_extract_suffix_empty() -> None:
    resp = types.SimpleNamespace(content=None)
    assert _extract_suffix("x", resp) == ""


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------

def _make_suggest(
    chat_fn=None,
    *,
    enabled: bool = True,
    model: str | None = "test-model",
) -> AIAutoSuggest:
    return AIAutoSuggest(
        model_name_provider=lambda: model,
        history_provider=lambda: [],
        enabled_provider=lambda: enabled,
        debounce_ms=0,  # disable debounce for tests
        chat_fn=chat_fn,
    )


def test_skip_too_short() -> None:
    s = _make_suggest()
    buf = Buffer()
    buf.text = "ab"
    doc = Document("ab", 2)
    assert s.get_suggestion(buf, doc) is None


def test_skip_slash_commands() -> None:
    s = _make_suggest()
    doc = Document("/think on", 8)
    buf = Buffer()
    buf.text = "/think on"
    assert s.get_suggestion(buf, doc) is None


def test_skip_at_mentions() -> None:
    s = _make_suggest()
    doc = Document("look at @file", 13)
    buf = Buffer()
    buf.text = "look at @file"
    assert s.get_suggestion(buf, doc) is None


def test_skip_multiline() -> None:
    s = _make_suggest()
    doc = Document("line one\nline two", 17)
    buf = Buffer()
    buf.text = "line one\nline two"
    assert s.get_suggestion(buf, doc) is None


def test_skip_when_disabled() -> None:
    s = _make_suggest(enabled=False)
    doc = Document("hello world", 11)
    buf = Buffer()
    buf.text = "hello world"
    assert s.get_suggestion(buf, doc) is None


def test_skip_when_no_model() -> None:
    s = _make_suggest(model=None)
    doc = Document("hello world", 11)
    buf = Buffer()
    buf.text = "hello world"
    assert s.get_suggestion(buf, doc) is None


# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------

def test_fetch_returns_suffix_and_caches() -> None:
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(), types.SimpleNamespace(content=" world")

    s = _make_suggest(chat_fn=fake_chat)
    buf = Buffer()
    buf.text = "hello"
    doc = Document("hello", 5)
    sug = s.get_suggestion(buf, doc)
    assert sug is not None
    # _extract_suffix strips whitespace, so the stored suffix is "world".
    assert sug.text == "world"
    # second call for the same text hits cache (no new chat call)
    s.get_suggestion(buf, doc)
    assert len(calls) == 1


def test_fetch_failure_returns_none() -> None:
    def fake_chat(**kwargs):
        raise RuntimeError("boom")

    s = _make_suggest(chat_fn=fake_chat)
    buf = Buffer()
    buf.text = "hello there"
    doc = Document("hello there", 11)
    assert s.get_suggestion(buf, doc) is None


def test_consecutive_failures_disable() -> None:
    def fake_chat(**kwargs):
        raise RuntimeError("boom")

    s = _make_suggest(chat_fn=fake_chat)
    buf = Buffer()
    for txt in ("hello there", "another one", "third input", "fourth try"):
        buf.text = txt
        doc = Document(txt, len(txt))
        s.get_suggestion(buf, doc)
    # After 3 failures, _disabled flips to True.
    assert s._disabled is True


def test_incremental_tail_shrinks_suggestion() -> None:
    def fake_chat(**kwargs):
        return types.SimpleNamespace(), types.SimpleNamespace(content=" world")

    s = _make_suggest(chat_fn=fake_chat)
    # Prime the cache for "hello" → suggestion "world" (after strip)
    buf = Buffer()
    buf.text = "hello"
    doc = Document("hello", 5)
    s.get_suggestion(buf, doc)
    # The cache now holds {"hello": "world"}. When the user types "hellow"
    # (continuing the suggestion without a separator), the "w" matches the
    # cached suggestion's first char, so we return "orld" with no new request.
    buf.text = "hellow"
    doc2 = Document("hellow", 6)
    sug = s.get_suggestion(buf, doc2)
    assert sug is not None
    assert sug.text == "orld"


def test_prompt_includes_current_input_and_system_constraint() -> None:
    captured = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(), types.SimpleNamespace(content="")

    s = _make_suggest(chat_fn=fake_chat)
    buf = Buffer()
    buf.text = "import json"
    doc = Document("import json", 11)
    s.get_suggestion(buf, doc)
    assert "import json" in captured["prompt"]
    assert "续写" in captured["system"] or "补全" in captured["system"]
    assert captured["thinking"] is False
