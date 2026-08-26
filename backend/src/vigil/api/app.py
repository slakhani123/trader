"""FastAPI application factory.

Every route is prefixed ``/api`` and requires a bearer token except
``GET /api/health`` (see docs/API_SPEC.md). Errors are always JSON
``{"detail": str}``; requests are logged through stdlib logging.
"""

from __future__ import annotations

import logging
import time

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vigil import __version__
from vigil.api.deps import require_auth
from vigil.api.routers import (
    admin,
    alerts,
    backtests,
    calendar,
    companies,
    health,
    instruments,
    opportunities,
    portfolio,
    signals,
)
from vigil.config import get_settings

log = logging.getLogger("vigil.api")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.getLogger("vigil").setLevel(settings.log_level.upper())

    app = FastAPI(
        title="Vigil API",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        log.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000.0,
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # /api/health carries no auth; /api/health/data declares it per-route.
    app.include_router(health.router, prefix="/api")
    for router in (
        instruments.router,
        companies.router,
        opportunities.router,
        alerts.router,
        signals.router,
        portfolio.router,
        calendar.router,
        backtests.router,
        admin.router,
    ):
        app.include_router(router, prefix="/api", dependencies=[Depends(require_auth)])
    return app
