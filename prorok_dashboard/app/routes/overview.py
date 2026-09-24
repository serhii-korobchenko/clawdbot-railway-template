from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from ..api_client import UpstreamError, UpstreamUnavailable
from ..auth import is_authenticated


router = APIRouter()
StatusFilter = Literal["active", "paused", "resolved", "archived"]
StatusQuery = Literal["", "active", "paused", "resolved", "archived"]
ActivityWindow = Literal["7d", "30d", "all"]
CandidateActivityMetric = Literal["accepted", "all"]


def _normalize_status(status: StatusQuery | None) -> StatusFilter | None:
    return status or None


async def _load(
    request: Request,
    *,
    status: str | None,
    q: str | None,
):
    return await request.app.state.prorok_api.list_events(
        status=status,
        q=q,
    )


@router.get("/")
async def overview(
    request: Request,
    status: StatusQuery | None = Query(default=None),
    q: str | None = Query(default=None, max_length=300),
    activity_window: ActivityWindow = Query(default="7d"),
    candidate_activity_window: ActivityWindow = Query(default="7d"),
    candidate_activity_metric: CandidateActivityMetric = Query(default="accepted"),
    candidate_activity_event: str | None = Query(default=None, max_length=200),
):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)

    templates = request.app.state.templates
    normalized_status = _normalize_status(status)

    try:
        data = await _load(request, status=normalized_status, q=q)
        evidence_activity = await request.app.state.prorok_api.get_evidence_activity(
            status="active",
            window=activity_window,
        )
        latest_refresh = await request.app.state.prorok_api.get_latest_refresh()
        candidate_activity = await request.app.state.prorok_api.get_candidate_activity(event_id=candidate_activity_event, window=candidate_activity_window, metric=candidate_activity_metric)
    except (UpstreamUnavailable, UpstreamError):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "PROROK data temporarily unavailable",
                "message": (
                    "Dashboard працює, але сервіс прогнозних даних "
                    "зараз недоступний."
                ),
            },
            status_code=503,
        )

    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "data": data,
            "latest_refresh": latest_refresh,
            "evidence_activity": evidence_activity,
            "activity_points": evidence_activity.get("items", []),
            "activity_window": activity_window,
            "candidate_activity": candidate_activity,
            "candidate_activity_window": candidate_activity_window,
            "candidate_activity_metric": candidate_activity_metric,
            "candidate_activity_event": candidate_activity_event or "",
            "status_filter": normalized_status,
            "q": q or "",
        },
    )


@router.get("/partials/events")
async def events_partial(
    request: Request,
    status: StatusQuery | None = Query(default=None),
    q: str | None = Query(default=None, max_length=300),
):
    if not is_authenticated(request):
        return PlainTextResponse("Unauthorized", status_code=401)

    templates = request.app.state.templates
    normalized_status = _normalize_status(status)

    try:
        data = await _load(request, status=normalized_status, q=q)
    except (UpstreamUnavailable, UpstreamError):
        return PlainTextResponse(
            "PROROK data temporarily unavailable",
            status_code=503,
        )

    return templates.TemplateResponse(
        request=request,
        name="partials/events_list.html",
        context={
            "data": data,
            "status_filter": normalized_status,
            "q": q or "",
        },
    )
