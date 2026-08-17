# Batch 3 — Tool & Streaming Input Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Stop two classes of crash/garbling from malformed input — a tool arg the model fabricates, and non-conforming provider streaming chunks — by guarding the unguarded `int()` casts and fixing two streaming-accumulator data bugs.

**Architecture:** `list_dir` casts `max_entries` with a bare `int()`; the streaming `_StreamAccumulator.consume()` casts `index` with a bare `int()` and overwrites (rather than accumulates) a split tool-call id; `to_response()` discards the raw chunks needed to debug streaming. All four are small, defensive, locale-independent.

**Tech Stack:** Python 3.11, `pytest`, `monkeypatch`.

**Environment note:** Test with `py -3.11 -m pytest <path> -v`. Branch: `fix/batch1-subprocess-stream-utf8` (continuing).

**Deferred (need full provider/httpx construction — separate mini-batch):** non-streaming `parse_chat_response` empty-`tool_calls`→`None` normalization (base.py:173); `health_check` treating `/models`→404 as healthy (base.py:97).

---

### Task 1: list_dir — guard non-numeric max_entries

`max_entries = min(int(args.get("max_entries", 200) or 200), _MAX_LIST_ENTRIES)` raises `ValueError` if the model passes `"abc"`. Guard it like `bash_tool` does for `timeout`.

**Files:**
- Modify: `modelbridge/agent/tools/file_tools.py:110`
- Test: `tests/test_file_tools.py` (append — file already exists)

- [ ] **Step 1: Write the failing test (append to tests/test_file_tools.py)**

```python


def test_list_dir_non_numeric_max_entries_falls_back(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    res = ListDirTool().execute({"path": ".", "max_entries": "abc"}, _ctx(tmp_path))
    assert not res.is_error
    assert "a.txt" in res.content
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_file_tools.py::test_list_dir_non_numeric_max_entries_falls_back -v`
Expected: FAIL — uncaught `ValueError: invalid literal for int() with base 10: 'abc'`.

- [ ] **Step 3: Guard the cast**

In `modelbridge/agent/tools/file_tools.py`, change line 110 from:

```python
        max_entries = min(int(args.get("max_entries", 200) or 200), _MAX_LIST_ENTRIES)
```

to:

```python
        try:
            max_entries = min(int(args.get("max_entries", 200) or 200), _MAX_LIST_ENTRIES)
        except (TypeError, ValueError):
            max_entries = min(200, _MAX_LIST_ENTRIES)
```

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_file_tools.py -v`
Expected: PASS (all file-tools tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_file_tools.py modelbridge/agent/tools/file_tools.py
git commit -m "fix(file_tools): guard non-numeric max_entries instead of crashing list_dir

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: streaming accumulator — three robustness fixes

Three independent fixes to `_StreamAccumulator` (a `@dataclass` at `modelbridge/providers/base.py:418`; construct via `_StreamAccumulator(provider="t", model_default="m")`). Do them as three TDD cycles in one test file, then commit once.

**Files:**
- Modify: `modelbridge/providers/base.py` (`consume` ~lines 467 and 472-473; `to_response` ~line 507)
- Test: `tests/test_stream_accumulator.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stream_accumulator.py
"""Robustness of the OpenAI streaming-chunk accumulator."""

from __future__ import annotations

from modelbridge.providers.base import _StreamAccumulator


def _acc():
    return _StreamAccumulator(provider="t", model_default="m")


def test_consume_tolerates_non_numeric_index():
    acc = _acc()
    chunk = {"choices": [{"delta": {"tool_calls": [
        {"index": "bad", "id": "c1", "function": {"name": "f", "arguments": "{}"}}
    ]}}]}
    acc.consume(chunk)  # must not raise
    assert 0 in acc.tool_calls
    assert acc.tool_calls[0]["function"]["name"] == "f"


def test_consume_accumulates_split_tool_call_id():
    acc = _acc()
    acc.consume({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_", "function": {"name": "f"}}
    ]}}]})
    acc.consume({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "123"}
    ]}}]})
    assert acc.tool_calls[0]["id"] == "call_123"


def test_to_response_preserves_raw_chunks():
    acc = _acc()
    acc.consume({"choices": [{"delta": {"content": "hi"}}]})
    resp = acc.to_response()
    assert resp.raw["chunks"] == 1          # count preserved (back-compat)
    assert resp.raw["raw_chunks"] == acc.raw_chunks  # actual chunks retained
```

- [ ] **Step 2: Run and verify they fail**

Run: `py -3.11 -m pytest tests/test_stream_accumulator.py -v`
Expected: `test_consume_tolerates_non_numeric_index` FAILS with `ValueError` (int("bad")); `test_consume_accumulates_split_tool_call_id` FAILS (`id` is `"123"`, overwritten); `test_to_response_preserves_raw_chunks` FAILS (`KeyError: 'raw_chunks'`).

- [ ] **Step 3: Apply the three fixes in `modelbridge/providers/base.py`**

(a) Guard the index cast. Change:
```python
                idx = int(tc.get("index", 0))
```
to:
```python
                try:
                    idx = int(tc.get("index", 0))
                except (TypeError, ValueError):
                    idx = 0
```

(b) Accumulate the id instead of overwriting. Change:
```python
                if tid := tc.get("id"):
                    slot["id"] = tid
```
to:
```python
                if tid := tc.get("id"):
                    slot["id"] = (slot["id"] or "") + tid
```

(c) Preserve raw chunks in `to_response`. Change:
```python
            raw={"chunks": len(self.raw_chunks)},
```
to:
```python
            raw={"chunks": len(self.raw_chunks), "raw_chunks": self.raw_chunks},
```

- [ ] **Step 4: Run and verify they pass**

Run: `py -3.11 -m pytest tests/test_stream_accumulator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_stream_accumulator.py modelbridge/providers/base.py
git commit -m "fix(providers): harden streaming accumulator (numeric index, split id, raw chunks)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Batch verification

- [ ] **Step 1: Full suite**

Run: `py -3.11 -m pytest -q`
Expected: all pass, no regressions.

- [ ] **Step 2: Confirm the unguarded casts are gone**

Run: `git grep -n "int(tc.get" modelbridge/providers/base.py`
Expected: the match is inside a `try:` block.

---

## Self-Review

- **Coverage (theme 6, clean subset):** list_dir guard ✓ (T1), non-numeric index ✓ (T2a), split-id accumulate ✓ (T2b), raw chunks ✓ (T2c). Deferred items (empty-`tool_calls` normalize, `health_check` 404) explicitly listed at top with rationale.
- **Placeholder scan:** none.
- **Type/name consistency:** `_StreamAccumulator(provider=, model_default=)` matches the dataclass at base.py:418; `ListDirTool`, `_ctx` match tests/test_file_tools.py.
