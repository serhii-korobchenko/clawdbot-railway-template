from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..api_client import (
    UpstreamError,
    UpstreamNotFound,
    UpstreamUnavailable,
)
from ..auth import is_authenticated
from ..view_models import chart_points, criteria_sections


router = APIRouter()


@router.get("/events/{event_id}")
async def event_detail(request: Request, event_id: str):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)

    templates = request.app.state.templates

    try:
        data = await request.app.state.prorok_api.get_event(event_id)
    except UpstreamNotFound:
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "title": "Подію не знайдено",
                "message": "Запитана подія відсутня у PROROK.",
            },
            status_code=404,
        )
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

    assessments = data.get("assessments") or []
    criteria = data.get("event", {}).get("decision_criteria") or {}

    return templates.TemplateResponse(
        request=request,
        name="event_detail.html",
        context={
            "data": data,
            "history": list(reversed(assessments)),
            "chart_data": chart_points(assessments),
            "criteria_sections": criteria_sections(criteria),
        },
    )
