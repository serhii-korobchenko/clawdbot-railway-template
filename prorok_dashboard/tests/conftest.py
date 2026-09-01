from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prorok_dashboard.app.app import create_app
from prorok_dashboard.app.config import DashboardSettings


class FakeApiClient:
    def __init__(self, settings):
        self.settings = settings

    async def start(self):
        return None

    async def close(self):
        return None

    async def health(self):
        return True

    async def list_events(self, *, status=None, q=None):
        items = [
            {
                "event_id": "event-1",
                "title": "Test event",
                "question": "Will it happen?",
                "status": "active",
                "forecast_horizon": "2026-12-31",
                "current_assessment": {
                    "assessment_id": 1,
                    "assessed_at": "2026-09-01T12:00:00Z",
                    "probability_percent": 40,
                    "probability_band": "40-50%",
                    "probability_label": "Реалістична можливість",
                    "confidence": "medium",
                    "delta_from_previous": 5,
                },
                "assessment_count": 1,
                "evidence_count": 1,
                "source_count": 1,
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-09-01T12:00:00Z",
                "archived_at": None,
            },
            {
                "event_id": "empty",
                "title": "No assessment",
                "question": "Nothing yet?",
                "status": "archived",
                "forecast_horizon": "2026-12-31",
                "current_assessment": None,
                "assessment_count": 0,
                "evidence_count": 0,
                "source_count": 0,
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-02T00:00:00Z",
                "archived_at": None,
            },
        ]
        if status:
            items = [item for item in items if item["status"] == status]
        if q:
            items = [item for item in items if q.lower() in (item["title"] + " " + item["question"]).lower()]
        return {
            "items": items,
            "total": 2,
            "filtered_total": len(items),
            "status_counts": {"active": 1, "paused": 0, "resolved": 0, "archived": 1},
        }

    async def get_event(self, event_id):
        if event_id == "missing":
            from prorok_dashboard.app.api_client import UpstreamNotFound
            raise UpstreamNotFound()

        return {
            "event": {
                "event_id": event_id,
                "title": "Test event",
                "question": "Will it happen?",
                "status": "active",
                "forecast_horizon": "2026-12-31",
                "tags": ["test"],
                "decision_criteria": {"format": "structured", "data": {"event_occurs_if": ["confirmed"], "does_not_count": []}, "raw": "{}"},
                "provenance": {"source_image_note": None},
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-09-01T12:00:00Z",
                "archived_at": None,
            },
            "current_assessment": {
                "assessment_id": 1,
                "assessed_at": "2026-09-01T12:00:00Z",
                "probability_percent": 40,
                "probability_band": "40-50%",
                "probability_label": "Реалістична можливість",
                "confidence": "medium",
                "delta_from_previous": 5,
            },
            "assessments": [{
                "assessment_id": 1, "run_id": 10, "assessed_at": "2026-09-01T12:00:00Z",
                "probability_percent": 40, "probability_band": "40-50%",
                "probability_label": "Реалістична можливість", "confidence": "medium",
                "delta_from_previous": 5, "rationale": "Because.",
            }],
            "evidence": [{
                "evidence_id": 1, "run_id": 11, "created_at": "2026-09-01T11:00:00Z",
                "direction": "indicator", "strength": "medium", "summary": "Evidence summary",
                "relevance": 80, "credibility": 90,
                "source": {"source_id": 1, "title": "Example", "domain": "example.com", "url": "https://example.com", "canonical_url": "https://example.com", "published_at": None, "source_type": "web"},
            }],
            "limitations": {"assessment_evidence_attribution": "unavailable"},
        }


@pytest.fixture
def dashboard_settings():
    return DashboardSettings(
        api_base_url="http://prorok.internal:18880",
        api_token="secret-upstream-token",
        password="dashboard-password",
        session_secret="session-secret-at-least-long-enough",
    )


@pytest.fixture
def client(dashboard_settings):
    app = create_app(dashboard_settings, api_client_factory=FakeApiClient)
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def logged_in_client(client):
    response = client.post("/login", data={"password": "dashboard-password"}, follow_redirects=False)
    assert response.status_code == 303
    return client
