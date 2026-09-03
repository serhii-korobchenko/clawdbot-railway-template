from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api_client import ProrokApiClient
from .config import DashboardSettings
from .routes import auth, events, overview
from .view_models import (
    delta_symbol,
    delta_text,
    format_date,
    format_datetime,
    safe_external_url,
)


ClientFactory = Callable[[DashboardSettings], ProrokApiClient]


def create_app(
    settings: DashboardSettings | None = None,
    api_client_factory: ClientFactory = ProrokApiClient,
) -> FastAPI:
    resolved_settings = settings or DashboardSettings.from_env()

    package_root = Path(__file__).resolve().parents[1]
    templates = Jinja2Templates(directory=str(package_root / "templates"))
    templates.env.filters["fmt_date"] = format_date
    templates.env.filters["fmt_datetime"] = format_datetime
    templates.env.filters["delta_text"] = delta_text
    templates.env.filters["delta_symbol"] = delta_symbol
    templates.env.filters["safe_external_url"] = safe_external_url

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = api_client_factory(resolved_settings)
        await client.start()
        app.state.prorok_api = client
        try:
            yield
        finally:
            await client.close()

    app = FastAPI(
        title="PROROK Dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.templates = templates

    app.add_middleware(
        SessionMiddleware,
        secret_key=resolved_settings.session_secret,
        session_cookie="prorok_session",
        max_age=12 * 60 * 60,
        same_site="strict",
        https_only=True,
    )

    app.mount(
        "/static",
        StaticFiles(directory=str(package_root / "static")),
        name="static",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        return response

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz(request: Request):
        if await request.app.state.prorok_api.health():
            return {"ok": True, "upstream": "reachable"}
        return JSONResponse(
            status_code=503,
            content={"ok": False, "upstream": "unavailable"},
        )

    app.include_router(auth.router)
    app.include_router(overview.router)
    app.include_router(events.router)

    return app
