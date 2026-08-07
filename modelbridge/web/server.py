"""FastAPI application factory.

Builds the app, mounts all routers under ``/api``, and serves the built
Next.js frontend from ``webui/.next/`` or ``webui/out/`` when present (so a
single ``mbridge web`` gives you API + UI on one port).
"""

from __future__ import annotations

from pathlib import Path


def build_app():  # pragma: no cover — integration tested via TestClient
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    from .routers import (
        config_router,
        doctor_router,
        models_router,
        prompts_router,
        session_router,
        skills_router,
        usage_router,
    )

    app = FastAPI(
        title="ModelBridge Admin",
        description="本地管理后台 API — 读写 ~/.modelbridge/ 配置。",
        version="1.0.0",
        default_response_class=JSONResponse,
    )

    # Permissive CORS: this server is localhost-only and the frontend may
    # run on a different port during development (e.g. :3000).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api_prefix = "/api"
    app.include_router(models_router, prefix=api_prefix)
    app.include_router(config_router, prefix=api_prefix)
    app.include_router(skills_router, prefix=api_prefix)
    app.include_router(prompts_router, prefix=api_prefix)
    app.include_router(doctor_router, prefix=api_prefix)
    app.include_router(usage_router, prefix=api_prefix)
    app.include_router(session_router, prefix=api_prefix)

    @app.get(api_prefix + "/health")
    def _health() -> dict:
        return {"ok": True, "service": "modelbridge-admin"}

    # Serve the built frontend if it exists alongside the repo.
    webui = _find_webui_dir()
    if webui is not None:
        app.mount("/", StaticFiles(directory=str(webui), html=True), name="webui")

    return app


def _find_webui_dir() -> Path | None:
    """Locate a built frontend (static export) to serve.

    Looks for ``webui/out`` (Next.js static export) relative to the repo
    root. Returns None during development (the frontend runs separately).
    """
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent.parent / "webui" / "out",
        here.parent.parent / "webui" / "out",
    ):
        if candidate.is_dir() and (candidate / "index.html").exists():
            return candidate
    return None
