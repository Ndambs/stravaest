"""FastAPI entry point:  uvicorn app.main:app --reload"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.routes import public, router
from .container import Container
from .integrations.strava.client import StravaError


class SPAStaticFiles(StaticFiles):
    """Serves the Angular build and falls back to index.html for client-side routes."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            if ex.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def create_app(container: Optional[Container] = None) -> FastAPI:
    c = container or Container()
    app = FastAPI(title="Training Hub API", version="1.0.0")
    app.state.container = c
    app.add_middleware(CORSMiddleware, allow_origins=list(c.settings.cors_origins), allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    @app.exception_handler(StravaError)
    async def strava_error(_: Request, exc: StravaError):
        return JSONResponse({"detail": str(exc), "rate_usage": exc.rate_usage}, status_code=exc.status if exc.status >= 400 else 502)

    app.include_router(public)
    app.include_router(router)
    dist = Path(c.settings.frontend_dist)
    if dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
