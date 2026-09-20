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
from .evidence_models import CandidateEvidenceListResponse, EvidenceActivityResponse, EvidenceListResponse
from .evidence_repository import evidence_activity, list_candidate_evidence, list_evidence
from .latest_refresh_models import LatestRefreshResponse
from .latest_refresh_repository import get_latest_refresh
from .models import (
    EventDetailResponse,
    EventListResponse,
    LatestRecommendationResponse,
)
from .repository import get_event_detail, get_latest_recommendation, list_events


EventStatusQuery = Literal["active", "paused", "resolved", "archived"]
EvidenceDirectionQuery = Literal["indicator", "counterindicator", "neutral"]
EvidenceActivityWindowQuery = Literal["7d", "30d", "all"]
CandidateDirectionQuery = Literal["indicator", "counterindicator"]
EvidenceStrengthQuery = Literal["weak", "medium", "strong"]
EvidenceSortQuery = Literal["newest", "oldest"]
CandidateValidationStateQuery = Literal[
    "legacy_unvalidated",
    "accepted",
    "rejected_source_policy",
    "rejected_date_conflict",
    "rejected_invalid_metadata",
]


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
        "/api/v1/evidence",
        response_model=EvidenceListResponse,
        dependencies=[Depends(require_api_token)],
    )
    def evidence_list(
        event_id: str | None = Query(default=None, max_length=200),
        direction: EvidenceDirectionQuery | None = Query(default=None),
        strength: EvidenceStrengthQuery | None = Query(default=None),
        source: str | None = Query(default=None, max_length=300),
        q: str | None = Query(default=None, max_length=300),
        sort: EvidenceSortQuery = Query(default="newest"),
    ):
        with readonly_connection(resolved_settings.db_path) as conn:
            return list_evidence(
                conn,
                event_id=event_id,
                direction=direction,
                strength=strength,
                source=source,
                q=q,
                sort=sort,
            )

    @app.get(
        "/api/v1/evidence/activity",
        response_model=EvidenceActivityResponse,
        dependencies=[Depends(require_api_token)],
    )
    def evidence_activity_summary(
        status: EventStatusQuery = Query(default="active"),
        window: EvidenceActivityWindowQuery = Query(default="7d"),
    ):
        with readonly_connection(resolved_settings.db_path) as conn:
            return evidence_activity(conn, status=status, window=window)

    @app.get(
        "/api/v1/evidence/candidates",
        response_model=CandidateEvidenceListResponse,
        dependencies=[Depends(require_api_token)],
    )
    def candidate_evidence_list(
        event_id: str | None = Query(default=None, max_length=200),
        direction: CandidateDirectionQuery | None = Query(default=None),
        strength: EvidenceStrengthQuery | None = Query(default=None),
        validation_state: CandidateValidationStateQuery | None = Query(default=None),
        source: str | None = Query(default=None, max_length=300),
        q: str | None = Query(default=None, max_length=300),
        sort: EvidenceSortQuery = Query(default="newest"),
    ):
        with readonly_connection(resolved_settings.db_path) as conn:
            return list_candidate_evidence(
                conn,
                event_id=event_id,
                direction=direction,
                strength=strength,
                validation_state=validation_state,
                source=source,
                q=q,
                sort=sort,
            )

    @app.get(
        "/api/v1/refresh/latest",
        response_model=LatestRefreshResponse,
        dependencies=[Depends(require_api_token)],
    )
    def refresh_latest():
        with readonly_connection(resolved_settings.db_path) as conn:
            return get_latest_refresh(conn)

    @app.get(
        "/api/v1/events/{event_id}/latest-recommendation",
        response_model=LatestRecommendationResponse,
        dependencies=[Depends(require_api_token)],
    )
    def event_latest_recommendation(event_id: str):
        with readonly_connection(resolved_settings.db_path) as conn:
            result = get_latest_recommendation(conn, event_id)

        if result is None:
            raise HTTPException(status_code=404, detail="Event not found")

        return result

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
