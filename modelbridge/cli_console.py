"""Shared rich consoles + Windows stdio bootstrap.

Importing this module reconfigures stdin/stdout/stderr to UTF-8 (Windows gbk
consoles can't encode the ✓ / ✗ / · glyphs in our rich output) *before* the
shared :data:`console` / :data:`err_console` are constructed. Every CLI module
imports these two singletons from here so they all render through the same,
correctly-encoded streams.
"""

from __future__ import annotations

import sys

from rich.console import Console

# Must run before any Console is created. No-op on platforms whose streams
# don't expose ``reconfigure`` (or are already UTF-8).
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

console = Console()
err_console = Console(stderr=True)
