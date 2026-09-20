from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from ..api_client import UpstreamError, UpstreamUnavailable
from ..auth import is_authenticated


router = APIRouter()
EvidenceTab = Literal["official", "candidates"]
DirectionQuery = Literal["", "indicator", "counterindicator", "neutral"]
CandidateDirectionQuery = Literal["", "indicator", "counterindicator"]
StrengthQuery = Literal["", "weak", "medium", "strong"]
SortQuery = Literal["newest", "oldest"]
ValidationStateQuery = Literal[
    "",
    "legacy_unvalidated",
    "accepted",
    "rejected_source_policy",
    "rejected_date_conflict",
    "rejected_invalid_metadata",
]


@router.get("/evidence")
async def evidence_page(
    request: Request,
    tab: EvidenceTab = Query(default="official"),
    event_id: str | None = Query(default=None, max_length=200),
    direction: str | None = Query(default=None, max_length=40),
    strength: StrengthQuery | None = Query(default=None),
    validation_state: ValidationStateQuery | None = Query(default=None),
    source: str | None = Query(default=None, max_length=300),
    q: str | None = Query(default=None, max_length=300),
    sort: SortQuery = Query(default="newest"),
):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)

    templates = request.app.state.templates
    normalized_event_id = event_id or None
    normalized_direction = direction or None
    normalized_strength = strength or None
    normalized_validation_state = validation_state or None
    normalized_source = source or None
    normalized_q = q or None

    try:
        events = await request.app.state.prorok_api.list_events()
        if tab == "candidates":
            data = await request.app.state.prorok_api.list_candidate_evidence(
                event_id=normalized_event_id,
                direction=normalized_direction,
                strength=normalized_strength,
                validation_state=normalized_validation_state,
                source=normalized_source,
                q=normalized_q,
                sort=sort,
            )
        else:
            data = await request.app.state.prorok_api.list_evidence(
                event_id=normalized_event_id,
                direction=normalized_direction,
                strength=normalized_strength,
                source=normalized_source,
                q=normalized_q,
                sort=sort,
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

    return templates.TemplateResponse(
        request=request,
        name="evidence.html",
        context={
            "data": data,
            "events": events["items"],
            "tab": tab,
            "event_id": normalized_event_id or "",
            "direction": normalized_direction or "",
            "strength": normalized_strength or "",
            "validation_state": normalized_validation_state or "",
            "source": normalized_source or "",
            "q": normalized_q or "",
            "sort": sort,
        },
    )
