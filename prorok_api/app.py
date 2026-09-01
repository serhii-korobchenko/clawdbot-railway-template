from __future__ import annotations

from contextlib import asynccontextmanager
import sqlite3
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .auth import require_api_token
from .config import ApiSettings
from .db import readonly_connection, validate_database
from .errors import DatabaseUnavailable
from .models import EventDetailResponse, EventListResponse
from .repository import get_event_detail, list_events


EventStatusQuery = Literal["active", "paused", "resolved", "archived"]


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    resolved_settings = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        validate_database(resolved_settings.db_path)
        app.state.settings = resolved_settings
        yield

    app = FastAPI(
        title="PROROK Read-Only API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/healthz")
    def healthz():
        try:
            with readonly_connection(resolved_settings.db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            return {"ok": True, "database": "reachable"}
        except (DatabaseUnavailable, sqlite3.Error):
            return JSONResponse(
                status_code=503,
                content={"ok": False, "database": "unavailable"},
            )

    @app.get(
        "/api/v1/events",
        response_model=EventListResponse,
        dependencies=[Depends(require_api_token)],
    )
    def events_list(
        status: EventStatusQuery | None = Query(default=None),
        q: str | None = Query(default=None, max_length=300),
    ):
        with readonly_connection(resolved_settings.db_path) as conn:
            return list_events(conn, status=status, q=q)

    @app.get(
        "/api/v1/events/{event_id}",
        response_model=EventDetailResponse,
        dependencies=[Depends(require_api_token)],
    )
    def event_detail(event_id: str):
        with readonly_connection(resolved_settings.db_path) as conn:
            result = get_event_detail(conn, event_id)

        if result is None:
            raise HTTPException(status_code=404, detail="Event not found")

        return result

    return app
