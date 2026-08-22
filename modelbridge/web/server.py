"""FastAPI application factory.

Builds the app, mounts all routers under ``/api``, and serves the built
Next.js frontend from ``webui/out`` when present (so a single
``mbridge web`` gives you API + UI on one port).
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from fastapi.staticfiles import StaticFiles
except ImportError:  # fastapi is optional; server.py is only used with it
    StaticFiles = object  # type: ignore[assignment, misc]


class CleanUrlStaticFiles(StaticFiles):  # type: ignore[misc, valid-type]
    """Static export serving with clean-URL support.

    Next's ``output: export`` emits flat ``usage.html`` files, and
    Starlette's ``StaticFiles(html=True)`` only maps *directories* to
    ``index.html`` — it never retries ``/usage`` as ``usage.html``. So a
    direct visit / refresh of any page route 404'd (in-app navigation
    masked this: Next's client router fetches ``usage.txt`` RSC payloads
    instead of hitting the route). This subclass retries extension-less
    404s as ``<path>.html`` and finally falls back to the export's own
    ``404.html`` (keeping the 404 status) so unknown routes at least look
    right.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code != 404 or path.startswith("api/"):
            return response
        if "." not in os.path.basename(path):
            response = await super().get_response(path + ".html", scope)
        if response.status_code == 404:
            try:
                response = await super().get_response("404.html", scope)
                response.status_code = 404
            except Exception:
                pass
        return response


def build_app():  # pragma: no cover — integration tested via TestClient
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

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
        app.mount(
            "/",
            CleanUrlStaticFiles(directory=str(webui), html=True),
            name="webui",
        )
    else:
        # Startup races a rebuild: ``next build``'s export phase empties
        # ``webui/out`` before rewriting it, and a server started in that
        # window would silently serve 404s for every page (API still fine).
        # Say it loudly instead, with the exact recovery command.
        import sys

        print(
            "[modelbridge] 未找到 webui/out 静态产物 — 本次仅启动 API，"
            "所有页面将 404。\n"
            "  若正在重建 webui：构建完成后重启 `mbridge web` 即可。\n"
            "  构建前端：cd webui && npm run build:static\n",
            file=sys.stderr,
        )

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
