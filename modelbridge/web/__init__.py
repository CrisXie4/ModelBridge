"""Local web admin backend for ModelBridge.

A FastAPI app that reads/writes the same ``~/.modelbridge/`` config that
the CLI uses, exposing a REST API for a bundled Next.js frontend.

Run via ``mbridge web`` (starts uvicorn on localhost). The import of
``fastapi`` is deferred to :func:`create_app` so the rest of the package
works without the web extras installed.
"""

from __future__ import annotations


def create_app():  # pragma: no cover — thin glue, exercised via integration
    """Build and return the configured FastAPI application.

    Imported lazily so ``import modelbridge.web`` does not require fastapi.
    """
    from .server import build_app

    return build_app()


__all__ = ["create_app"]
