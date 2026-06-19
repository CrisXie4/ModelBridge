# Batch 2 — Router Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix five real correctness bugs in ModelBridge's routing (the flagship feature) so prompts map to the right model tier and the LLM classifier is robust and consistent across entry points.

**Architecture:** Two classifiers exist — keyword (`router/classifier.py`) and LLM (`router/llm_classifier.py`). We fix: (1) a file-count dedup bug that over-escalates, (2) substring keyword matches that false-fire, (3) brittle JSON extraction, (4) an ignored `context_tokens` signal, and (5) the MCP `route` tool silently using a different classifier than the CLI.

**Tech Stack:** Python 3.11, `re`, `json`, `pytest`, `monkeypatch`.

**Environment note:** Run tests with `py -3.11 -m pytest <path> -v` (the env with all deps). Branch: `fix/batch1-subprocess-stream-utf8` (continuing on it).

---

### Task 1: classifier — count UNIQUE files, not regex extension groups

`_FILE_GLOB` has a capture group `(py|ts|…)`, so `findall()` returns extension strings, and the same file mentioned twice yields `["py","py"]` → `len >= 2` → AGENT. Count unique full matches instead.

**Files:**
- Modify: `modelbridge/router/classifier.py:212`
- Test: `tests/test_classifier_routing.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classifier_routing.py
"""Routing-classifier correctness regressions."""

from __future__ import annotations

from modelbridge.models import ModelLevel
from modelbridge.router.classifier import classify_task


def test_same_file_twice_does_not_escalate_to_agent():
    # One file mentioned twice is a single-file edit → CODER, not AGENT.
    p = classify_task("please change src/utils.py then change src/utils.py once more")
    assert p.recommended_level == ModelLevel.CODER


def test_two_distinct_files_escalate_to_agent():
    p = classify_task("please change src/a.py and also tests/b.py")
    assert p.recommended_level == ModelLevel.AGENT
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_classifier_routing.py::test_same_file_twice_does_not_escalate_to_agent -v`
Expected: FAIL — currently returns AGENT (counts `["py","py"]`).

- [ ] **Step 3: Fix — dedupe unique file matches**

In `modelbridge/router/classifier.py`, change line 212 from:

```python
    file_hits = _FILE_GLOB.findall(text)
```

to:

```python
    file_hits = {m.group(0) for m in _FILE_GLOB.finditer(text)}
```

Then update the two reason strings (lines 214 and 218) to say files, not suffixes:
- Line 214: `reasons.append(f"提到 ≥2 个源文件 ({len(file_hits)})")`
- Line 218: `reasons.append("提到 1 个源文件")`

(`len(file_hits)` and `len(file_hits) == 1` still work — a set has a length.)

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_classifier_routing.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_classifier_routing.py modelbridge/router/classifier.py
git commit -m "fix(router): count unique files, not regex extension groups, when escalating

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: classifier — keyword matching uses word boundaries for ASCII

`w in text` matches substrings, so `class` fires inside `classifier`, `patch` inside `dispatch`, etc. Use `\b` boundaries for ASCII keywords; keep substring matching for CJK (where `\b` doesn't apply).

**Files:**
- Modify: `modelbridge/router/classifier.py` (the keyword-scan loop around line 188; add a helper near it)
- Test: `tests/test_classifier_routing.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python


def test_ascii_keyword_does_not_substring_false_match():
    # 'class' must NOT match inside 'classifier'.
    p = classify_task("the classifier is a bit slow today")
    assert p.recommended_level == ModelLevel.CHEAP
    assert "class" not in p.matched_keywords


def test_standalone_ascii_keyword_still_matches():
    p = classify_task("write a class for the User model")
    assert p.task_type == "code_generate"


def test_cjk_keyword_still_substring_matches():
    # CJK keywords have no word boundary; substring matching must still work.
    p = classify_task("帮我写代码实现一个登录函数")
    assert p.task_type == "code_generate"
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_classifier_routing.py::test_ascii_keyword_does_not_substring_false_match -v`
Expected: FAIL — `class` substring-matches `classifier`, routing to CODER and putting `class` in matched_keywords.

- [ ] **Step 3: Add a boundary-aware matcher and use it**

In `modelbridge/router/classifier.py`, add this helper just above `def classify_task(` (after the `_bump_complexity` function):

```python
def _keyword_hit(word: str, text: str, lowered: str) -> bool:
    """True if ``word`` occurs in the prompt.

    ASCII keywords require word boundaries so 'class' doesn't fire inside
    'classifier' or 'patch' inside 'dispatch'. CJK keywords have no usable
    word boundary, so they keep plain substring matching.
    """
    if not word.isascii():
        return word in text or word.lower() in lowered
    return re.search(rf"\b{re.escape(word.lower())}\b", lowered) is not None
```

Then change the keyword-scan line (currently line 188):

```python
        hits = [w for w in words if w in text or w.lower() in lowered]
```

to:

```python
        hits = [w for w in words if _keyword_hit(w, text, lowered)]
```

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_classifier_routing.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add tests/test_classifier_routing.py modelbridge/router/classifier.py
git commit -m "fix(router): word-boundary match for ASCII keywords to stop substring false positives

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: llm_classifier — robust JSON extraction (first complete object)

The fallback uses a greedy `\{.*\}` regex that spans first `{` to last `}`, so two objects or trailing prose make `json.loads` fail. Use `JSONDecoder().raw_decode` from the first `{` to grab the first complete object (handles nesting + trailing content).

**Files:**
- Modify: `modelbridge/router/llm_classifier.py` (the `_extract_json` fallback at lines 90-98; remove now-unused `import re` at line 24)
- Test: `tests/test_llm_classifier_robustness.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_classifier_robustness.py
"""LLM-classifier JSON extraction + context-token robustness."""

from __future__ import annotations

import pytest

from modelbridge.router.llm_classifier import LLMClassifyError, _extract_json


def test_extract_json_ignores_trailing_second_object():
    assert _extract_json('{"task_type":"chat"} {"x":1}') == {"task_type": "chat"}


def test_extract_json_with_prose_prefix():
    assert _extract_json('Sure! {"task_type":"chat"} done') == {"task_type": "chat"}


def test_extract_json_handles_nested_object():
    assert _extract_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_extract_json_raises_on_garbage():
    with pytest.raises(LLMClassifyError):
        _extract_json("no json at all")
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_llm_classifier_robustness.py::test_extract_json_ignores_trailing_second_object -v`
Expected: FAIL — greedy match spans both objects, `json.loads` fails, `LLMClassifyError` raised instead of returning `{"task_type":"chat"}`.

- [ ] **Step 3: Replace the greedy regex with raw_decode**

In `modelbridge/router/llm_classifier.py`, replace the fallback block (lines 90-98, the `# Fallback:` comment through its `pass`):

```python
    # Fallback: grab the outermost { ... } span and try that.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
```

with:

```python
    # Fallback: decode the first complete JSON object starting at the first
    # '{'. raw_decode stops at the end of that object, so trailing prose or a
    # second object is ignored, and nested objects are handled correctly.
    start = raw.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
```

Then remove the now-unused `import re` (line 24) — confirm with `py -3.11 -c "import ast,sys; ..."` is unnecessary; just grep the file for other `re.` uses; there are none, so delete the line `import re`.

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_llm_classifier_robustness.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_llm_classifier_robustness.py modelbridge/router/llm_classifier.py
git commit -m "fix(router): extract first complete JSON object from classifier reply, not greedy span

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: llm_classifier — honor context_tokens (parity with keyword path)

`classify_task_llm` accepts `context_tokens` but marks it `ARG001` and ignores it; the keyword classifier bumps to AGENT for `> 32000`. Apply the same floor.

**Files:**
- Modify: `modelbridge/router/llm_classifier.py` (signature line 110; add a floor among the caller-fact floors ~line 189)
- Test: `tests/test_llm_classifier_robustness.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python


def test_llm_classifier_honors_large_context_tokens(monkeypatch):
    from types import SimpleNamespace

    from modelbridge.models import ModelLevel
    from modelbridge.router import llm_classifier as llm_mod
    from modelbridge.router.llm_classifier import classify_task_llm

    monkeypatch.setattr(
        llm_mod, "resolve_with_fallback",
        lambda level: SimpleNamespace(chosen_model="tiny-x"),
    )

    def fake_chat_once(prompt, **kwargs):
        resp = SimpleNamespace(
            content='{"task_type":"chat","complexity":"simple",'
                    '"risk_level":"low","recommended_level":"cheap","reason":"t"}'
        )
        return (SimpleNamespace(), resp)

    monkeypatch.setattr(llm_mod, "chat_once", fake_chat_once)

    p = classify_task_llm("hi there", context_tokens=40000)
    assert p.recommended_level == ModelLevel.AGENT
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest "tests/test_llm_classifier_robustness.py::test_llm_classifier_honors_large_context_tokens" -v`
Expected: FAIL — level stays `cheap` (context_tokens ignored).

- [ ] **Step 3: Implement the floor**

In `modelbridge/router/llm_classifier.py`:

(a) Change the signature line 110 from:

```python
    context_tokens: int = 0,  # noqa: ARG001 - accepted for signature parity
```

to:

```python
    context_tokens: int = 0,
```

(b) Add this block immediately AFTER the `if wants_tools or wants_mcp:` floor and BEFORE the `if previous_failures >= 2:` block (around line 189), mirroring the keyword classifier:

```python
    if context_tokens > 32000:
        reasons.append(f"上下文 tokens 较大 ({context_tokens})")
        level = max(level, ModelLevel.AGENT, key=_LEVEL_ORDER.index)
        complexity = "hard"
```

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_llm_classifier_robustness.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tests/test_llm_classifier_robustness.py modelbridge/router/llm_classifier.py
git commit -m "fix(router): LLM classifier honors context_tokens floor (parity with keyword path)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: MCP route tool — use the LLM classifier, matching the CLI

The MCP `route` tool calls `route(prompt)` (keyword default) while the CLI passes `use_llm=True`. Same input → different result by interface. Make the tool consistent. If the tiny model is unconfigured, `route` raises `LLMClassifyError`, which the MCP server already converts to an `isError` tool response — a clear error, consistent with the project's LLM-only contract.

**Files:**
- Modify: `modelbridge/mcp/server/builtin.py:56`
- Test: `tests/test_mcp_route_tool.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_route_tool.py
"""The MCP `route` tool must use the LLM classifier, like the CLI."""

from __future__ import annotations

from types import SimpleNamespace

import modelbridge.router as router_mod
from modelbridge.mcp.server.builtin import _tool_route


def test_mcp_route_tool_passes_use_llm_true(monkeypatch):
    captured = {}

    def fake_route(prompt, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            profile=SimpleNamespace(task_type="chat", reasons=["r"]),
            level=SimpleNamespace(value="cheap"),
            chosen_model="m",
        )

    monkeypatch.setattr(router_mod, "route", fake_route)
    out = _tool_route({"prompt": "hello world"})

    assert captured.get("use_llm") is True
    assert '"chosen_model": "m"' in out
```

- [ ] **Step 2: Run and verify it fails**

Run: `py -3.11 -m pytest tests/test_mcp_route_tool.py -v`
Expected: FAIL — `captured.get("use_llm")` is `None` (current call passes no `use_llm`).

- [ ] **Step 3: Pass use_llm=True**

In `modelbridge/mcp/server/builtin.py`, change line 56 from:

```python
    r = route(prompt)
```

to:

```python
    r = route(prompt, use_llm=True)
```

- [ ] **Step 4: Run and verify it passes**

Run: `py -3.11 -m pytest tests/test_mcp_route_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_mcp_route_tool.py modelbridge/mcp/server/builtin.py
git commit -m "fix(mcp/route): use the LLM classifier so MCP routing matches the CLI

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Batch verification

- [ ] **Step 1: Full suite**

Run: `py -3.11 -m pytest -q`
Expected: all pass, no regressions (note existing routing tests in `tests/test_classifier.py` / `tests/test_llm_classifier.py` must stay green — if a fix changes a documented behavior they assert, reconcile by reading that test before editing).

- [ ] **Step 2: Confirm no stray greedy regex / findall remains**

Run: `git grep -n "_FILE_GLOB.findall\|\\\\{.\\*\\\\}" modelbridge/router/`
Expected: no matches.

---

## Self-Review

- **Coverage (theme 2):** file dedup ✓ (T1), word-boundary keywords ✓ (T2), greedy JSON ✓ (T3), context_tokens ✓ (T4), MCP/CLI classifier parity ✓ (T5). The 6th theme-2 finding (`assert` for runtime validation at cli.py:1367/1392) is intentionally deferred to the CLI-IA batch to avoid double-touching cli.py.
- **Placeholder scan:** none — full test + fix code in every step.
- **Type/name consistency:** `classify_task`, `classify_task_llm`, `_extract_json`, `_tool_route`, `_keyword_hit`, `ModelLevel.{CODER,AGENT,CHEAP}` all match the source read during planning. `_keyword_hit` defined in T2 and used in the same edit.
