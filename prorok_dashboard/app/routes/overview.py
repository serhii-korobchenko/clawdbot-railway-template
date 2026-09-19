from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from ..api_client import UpstreamError, UpstreamUnavailable
from ..auth import is_authenticated


router = APIRouter()
StatusFilter = Literal["active", "paused", "resolved", "archived"]
StatusQuery = Literal["", "active", "paused", "resolved", "archived"]


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
):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)

    templates = request.app.state.templates
    normalized_status = _normalize_status(status)

    try:
        data = await _load(request, status=normalized_status, q=q)
        activity_data = await _load(request, status="active", q=None)
        latest_refresh = await request.app.state.prorok_api.get_latest_refresh()
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

    activity_points = sorted(
        [
            {
                "event_id": item.get("event_id"),
                "title": item.get("title") or item.get("event_id") or "Без назви",
                "evidence_count": int(item.get("evidence_count") or 0),
            }
            for item in activity_data.get("items", [])
        ],
        key=lambda item: (-item["evidence_count"], item["title"].casefold()),
    )

    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "data": data,
            "latest_refresh": latest_refresh,
            "activity_points": activity_points,
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
