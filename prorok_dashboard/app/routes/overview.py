from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from ..api_client import UpstreamError, UpstreamUnavailable
from ..auth import is_authenticated


router = APIRouter()
StatusFilter = Literal["active", "paused", "resolved", "archived"]


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
    status: StatusFilter | None = Query(default=None),
    q: str | None = Query(default=None, max_length=300),
):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)

    templates = request.app.state.templates

    try:
        data = await _load(request, status=status, q=q)
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
            "status_filter": status,
            "q": q or "",
        },
    )


@router.get("/partials/events")
async def events_partial(
    request: Request,
    status: StatusFilter | None = Query(default=None),
    q: str | None = Query(default=None, max_length=300),
):
    if not is_authenticated(request):
        return PlainTextResponse("Unauthorized", status_code=401)

    templates = request.app.state.templates

    try:
        data = await _load(request, status=status, q=q)
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
            "status_filter": status,
            "q": q or "",
        },
    )
