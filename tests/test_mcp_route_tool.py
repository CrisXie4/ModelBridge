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
