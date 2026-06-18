"""Regression test: SamplingService must enforce sampling_max_calls under concurrency.

The bug: handle() did an unlocked read-check-increment on self._counts, so two
threads could both read the same count, both pass the ceiling check, and both
increment — letting a server exceed the per-server ceiling.

The fix: wrap the read-check-increment in a threading.Lock.

This test makes the race DETERMINISTIC by replacing _counts with a _RaceDict
whose __setitem__ sleeps briefly.  The race window is between `.get()` returning
and `__setitem__` being called: without the lock, thread A reads the old value,
then sleeps during the write — freeing the GIL so thread B also reads the old
value and passes the ceiling check.  With the lock, thread A holds the lock
throughout the read-check-write; thread B blocks at lock acquisition and only
reads AFTER thread A's write completes, so it sees the updated count.
"""
from __future__ import annotations

import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from modelbridge.mcp import sampling as sampling_mod
from modelbridge.mcp.config import MCPSettings


# ---------------------------------------------------------------------------
# Race-widening dict: sleeping in __setitem__ releases the GIL between the
# check and the write, letting other threads read the stale value.
# ---------------------------------------------------------------------------

class _RaceDict(dict):
    """A dict whose __setitem__ sleeps to widen the concurrency race window.

    Without an external lock, threads that have already passed the ceiling
    check will run their reads during this sleep and also pass the check.
    With a lock wrapping get/check/setitem, the lock is held during the sleep
    so other threads block and only see the updated value afterward.
    """
    def __setitem__(self, k, v):
        time.sleep(0.02)  # releases GIL; other threads can run their reads
        super().__setitem__(k, v)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CALLS = 1
NUM_THREADS = 5   # 5 is enough; _RaceDict makes the race deterministic

_SAMPLE_PARAMS = {"messages": [{"role": "user", "content": "hi"}]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> sampling_mod.SamplingService:
    settings = MCPSettings(sampling_max_calls=MAX_CALLS)
    return sampling_mod.SamplingService(settings)


def _stub_model_calls(monkeypatch):
    """Patch out all network calls so handle() returns immediately after the lock."""
    monkeypatch.setattr(
        sampling_mod, "resolve_model_name", lambda m: "stub-model"
    )
    monkeypatch.setattr(
        sampling_mod, "get_model_entry", lambda n: types.SimpleNamespace(model="stub-model")
    )
    monkeypatch.setattr(
        sampling_mod,
        "get_provider",
        lambda e: types.SimpleNamespace(
            chat=lambda req, **kw: types.SimpleNamespace(content="ok", model="stub-model")
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ceiling_enforced_under_concurrency(monkeypatch):
    """With the lock, exactly MAX_CALLS calls succeed even under heavy concurrency.

    Without the fix this test fails: _RaceDict.__setitem__ sleeps while holding
    no lock, so all NUM_THREADS threads read the same stale count, all pass the
    ceiling check, and all succeed — producing NUM_THREADS successes instead of
    MAX_CALLS.  With the lock the sleep happens inside the lock, threads block at
    lock-acquisition, and exactly MAX_CALLS succeed.
    """
    _stub_model_calls(monkeypatch)
    svc = _make_service()
    svc._counts = _RaceDict()

    successes = 0
    errors = 0
    counter_lock = threading.Lock()

    def call_handle():
        nonlocal successes, errors
        try:
            svc.handle("s1", _SAMPLE_PARAMS)
            with counter_lock:
                successes += 1
        except ValueError:
            with counter_lock:
                errors += 1

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as pool:
        futures = [pool.submit(call_handle) for _ in range(NUM_THREADS)]
        for f in as_completed(futures):
            f.result()  # re-raise unexpected exceptions

    assert successes == MAX_CALLS, (
        f"Expected exactly {MAX_CALLS} successful call(s), got {successes} "
        f"(errors={errors}). The per-server sampling ceiling is broken under concurrency."
    )
    assert errors == NUM_THREADS - MAX_CALLS


def test_ceiling_without_race_condition(monkeypatch):
    """Sequential calls also respect the ceiling (sanity check, no concurrency)."""
    _stub_model_calls(monkeypatch)
    svc = _make_service()

    # First call should succeed
    result = svc.handle("s1", _SAMPLE_PARAMS)
    assert result["role"] == "assistant"

    # Second call should be denied
    with pytest.raises(ValueError, match="sampling 调用已达本会话上限"):
        svc.handle("s1", _SAMPLE_PARAMS)


def test_ceiling_independent_per_server(monkeypatch):
    """Each server_id gets its own independent counter."""
    _stub_model_calls(monkeypatch)
    settings = MCPSettings(sampling_max_calls=1)
    svc = sampling_mod.SamplingService(settings)

    # Both s1 and s2 should each get one successful call
    svc.handle("s1", _SAMPLE_PARAMS)
    svc.handle("s2", _SAMPLE_PARAMS)

    with pytest.raises(ValueError):
        svc.handle("s1", _SAMPLE_PARAMS)
    with pytest.raises(ValueError):
        svc.handle("s2", _SAMPLE_PARAMS)
