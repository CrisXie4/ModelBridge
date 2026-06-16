# tests/test_mcp_server_reconfigure.py
"""_reconfigure_stdio must not crash when reconfigure() raises ValueError."""

from __future__ import annotations

from modelbridge.mcp.server import server as server_mod


class _BadStream:
    def reconfigure(self, **kwargs):
        raise ValueError("I/O operation on closed file")


def test_reconfigure_stdio_swallows_valueerror(monkeypatch):
    monkeypatch.setattr(server_mod.sys, "stdin", _BadStream())
    monkeypatch.setattr(server_mod.sys, "stdout", _BadStream())
    # Must return without raising.
    server_mod._reconfigure_stdio()
