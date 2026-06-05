"""Namespacing + conflict resolution for multi-server catalogs.

Every MCP item is exposed under a qualified name ``<server_id>__<name>`` so two
servers offering a tool called ``search`` never collide, and the model's tool
choice is unambiguous. The double underscore is the separator (single
underscores are common inside tool names).

Tool names must also satisfy the OpenAI function-name charset
(``^[a-zA-Z0-9_-]+$``); we sanitise the server_id and tool name into that set.
"""

from __future__ import annotations

import re

SEP = "__"
_INVALID = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize(part: str) -> str:
    cleaned = _INVALID.sub("_", part.strip())
    return cleaned or "x"


def qualify(server_id: str, name: str) -> str:
    return f"{sanitize(server_id)}{SEP}{sanitize(name)}"


def split_qualified(qualified: str) -> tuple[str, str] | None:
    """Inverse of :func:`qualify` on the *sanitised* server_id.

    Returns ``(server_id_part, name_part)`` or ``None`` if unqualified. Because
    sanitisation isn't reversible, the manager resolves the real server_id by
    matching the prefix against its registered (sanitised) ids — see
    :meth:`Catalog.resolve_tool`.
    """
    if SEP not in qualified:
        return None
    server_part, name_part = qualified.split(SEP, 1)
    return server_part, name_part


__all__ = ["SEP", "sanitize", "qualify", "split_qualified"]
