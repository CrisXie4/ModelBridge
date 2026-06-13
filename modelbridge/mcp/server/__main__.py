"""``python -m modelbridge.mcp.server`` — run the built-in MCP server on stdio."""

from __future__ import annotations

from .builtin import build_modelbridge_server


def main() -> int:
    return build_modelbridge_server().serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
