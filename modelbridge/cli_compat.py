"""CLI backward-compat: register old command names as hidden, deprecating aliases.

Used by the information-architecture refactor (R2/R3). Typer builds the CLI
from ``impl``'s signature (via ``functools.wraps`` → ``__wrapped__``), so a
``*args/**kwargs`` wrapper accepts exactly the same arguments as the command it
forwards to. Validated on Typer 0.25.x.
"""

from __future__ import annotations

import functools
from typing import Callable

import typer

from .cli_console import err_console


def deprecated_alias(
    app: typer.Typer, old: str, new: str, impl: Callable, **command_kwargs
) -> Callable:
    """Register ``old`` as a HIDDEN command that warns to stderr then runs ``impl``.

    ``old`` keeps working (same args as ``impl``) but prints a one-line
    deprecation notice and is absent from ``--help``. Remove these in v2.0.
    """

    @app.command(old, hidden=True, **command_kwargs)
    @functools.wraps(impl)
    def _wrapper(*args, **kwargs):
        err_console.print(
            f"[yellow]⚠ `mbridge {old}` 已移至 `mbridge {new}`，将在 v2.0 移除。[/yellow]"
        )
        return impl(*args, **kwargs)

    return _wrapper
