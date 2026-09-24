"""FastAPI-applikationen."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from redovisningai.api import routes_company, routes_other, routes_portfolio, routes_review
from redovisningai.config import get_settings

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # API:t returnerar aldrig HTML; strikt CSP som extra skydd.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app() -> FastAPI:
    s = get_settings()
    if problems := s.production_problems():
        raise RuntimeError("Osäker konfiguration för produktion: " + "; ".join(problems))
    app = FastAPI(
        title="RedovisningAI",
        version="0.1.0",
        description="Granskningsverktyg för redovisningsbyråer. Alla belopp är strängar med exakta decimaler.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Dev-User", "X-Org-Id"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Ohanterat fel på %s", request.url.path)
        return JSONResponse({"detail": "Internt fel. Försök igen eller kontakta support."}, status_code=500)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(routes_portfolio.router)
    app.include_router(routes_company.router)
    app.include_router(routes_company.bulk_router)
    app.include_router(routes_company.callback_router)
    app.include_router(routes_review.router)
    app.include_router(routes_other.public)
    app.include_router(routes_other.reports)
    app.include_router(routes_other.admin)
    app.include_router(routes_other.aml)
    return app


app = create_app()
