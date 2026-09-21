def test_official_evidence_page(logged_in_client):
    async def list_evidence(**kwargs):
        assert kwargs["direction"] == "indicator"
        assert kwargs["sort"] == "newest"
        return {
            "items": [
                {
                    "evidence_id": 42,
                    "run_id": 7,
                    "created_at": "2026-09-17T08:30:00Z",
                    "direction": "indicator",
                    "strength": "strong",
                    "summary": "Official evidence summary",
                    "relevance": 95,
                    "credibility": 90,
                    "assessment": {
                        "status": "unknown",
                        "provenance_type": None,
                        "baseline_probability": None,
                        "selected_probability": None,
                        "refresh_id": None,
                        "assessment_id": None,
                        "decided_at": None,
                    },
                    "event": {
                        "event_id": "event-1",
                        "title": "Test event",
                        "status": "active",
                    },
                    "source": {
                        "source_id": 12,
                        "title": "Trusted source",
                        "domain": "example.com",
                        "url": "https://example.com/evidence",
                        "canonical_url": "https://example.com/evidence",
                        "published_at": "2026-09-17T07:00:00Z",
                        "source_type": "web",
                    },
                }
            ],
            "total": 3,
            "filtered_total": 1,
        }

    logged_in_client.app.state.prorok_api.list_evidence = list_evidence
    response = logged_in_client.get("/evidence?direction=indicator")

    assert response.status_code == 200
    assert "Official evidence summary" in response.text
    assert "Trusted source" in response.text
    assert "Evidence #42" in response.text
    assert "Candidates" in response.text
    assert "secret-upstream-token" not in response.text


def test_candidate_evidence_page(logged_in_client):
    async def list_candidate_evidence(**kwargs):
        assert kwargs["validation_state"] == "rejected_source_policy"
        assert kwargs["sort"] == "oldest"
        return {
            "items": [
                {
                    "candidate_id": 9,
                    "refresh_event_result_id": 87,
                    "refresh_id": 24,
                    "event_id": "event-1",
                    "event_title": "Test event",
                    "ordinal": 2,
                    "direction": "indicator",
                    "strength": "medium",
                    "relevance": 70,
                    "credibility": 60,
                    "title": "Rejected candidate",
                    "source": "Facebook post",
                    "url": "https://www.facebook.com/example",
                    "published_at": "2026-09-16T10:00:00Z",
                    "summary": "Candidate summary",
                    "why_it_matters": "Potential signal",
                    "duplicate_risk": "low",
                    "freshness": "missed_baseline_evidence",
                    "validation_state": "rejected_source_policy",
                    "rejection_reason": "domain is banned",
                    "created_at": "2026-09-17T08:35:00Z",
                    "decision_id": None,
                    "decision_type": None,
                    "selected_probability": None,
                    "decided_at": None,
                    "promotion_action": None,
                    "evidence_id": None,
                    "promoted_at": None,
                }
            ],
            "total": 5,
            "filtered_total": 1,
        }

    logged_in_client.app.state.prorok_api.list_candidate_evidence = list_candidate_evidence
    response = logged_in_client.get(
        "/evidence?tab=candidates&lifecycle=processed&validation_state=rejected_source_policy&sort=oldest"
    )

    assert response.status_code == 200
    assert "Candidate evidence" in response.text
    assert "Rejected candidate" in response.text
    assert "rejected_source_policy" in response.text
    assert "domain is banned" in response.text
    assert "Refresh #24" in response.text
    assert "Старі спочатку" in response.text
    assert "Decision: not applicable" in response.text
    assert "Official evidence: not promoted" in response.text


def test_candidate_evidence_promoted_lifecycle(logged_in_client):
    async def list_candidate_evidence(**kwargs):
        return {
            "items": [{
                "candidate_id": 28,
                "refresh_event_result_id": 99,
                "refresh_id": 44,
                "event_id": "event-1",
                "event_title": "Test event",
                "ordinal": 1,
                "direction": "indicator",
                "strength": "strong",
                "relevance": 90,
                "credibility": 85,
                "title": "Accepted candidate",
                "source": "Trusted source",
                "url": "https://example.com/item",
                "published_at": "2026-09-17T10:00:00Z",
                "summary": "Accepted summary",
                "why_it_matters": "Material signal",
                "duplicate_risk": "low",
                "freshness": "new",
                "validation_state": "accepted",
                "rejection_reason": None,
                "created_at": "2026-09-17T11:00:00Z",
                "decision_id": 12,
                "decision_type": "keep_current",
                "selected_probability": 35,
                "decided_at": "2026-09-17T12:00:00Z",
                "promotion_action": "inserted",
                "evidence_id": 123,
                "promoted_at": "2026-09-17T12:00:01Z",
            }],
            "total": 1,
            "filtered_total": 1,
        }

    logged_in_client.app.state.prorok_api.list_candidate_evidence = list_candidate_evidence
    response = logged_in_client.get("/evidence?tab=candidates&lifecycle=processed")

    assert response.status_code == 200
    assert "Decision: keep_current · 35%" in response.text
    assert "Official evidence: Evidence #123 · inserted" in response.text


def test_evidence_requires_authentication(client):
    response = client.get("/evidence", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_candidate_evidence_defaults_to_pending(logged_in_client):
    async def list_candidate_evidence(**kwargs):
        return {
            "items": [
                {
                    "candidate_id": 1,
                    "refresh_event_result_id": 1,
                    "refresh_id": 1,
                    "event_id": "event-1",
                    "event_title": "Test event",
                    "ordinal": 1,
                    "direction": "indicator",
                    "strength": "medium",
                    "relevance": 80,
                    "credibility": 80,
                    "title": "Pending candidate",
                    "source": "Source",
                    "url": "https://example.com/pending",
                    "published_at": None,
                    "summary": "Pending summary",
                    "why_it_matters": None,
                    "duplicate_risk": "low",
                    "freshness": "new",
                    "validation_state": "accepted",
                    "rejection_reason": None,
                    "created_at": "2026-09-20T10:00:00Z",
                    "decision_id": None,
                    "decision_type": None,
                    "selected_probability": None,
                    "decided_at": None,
                    "promotion_action": None,
                    "evidence_id": None,
                    "promoted_at": None,
                },
                {
                    "candidate_id": 2,
                    "refresh_event_result_id": 2,
                    "refresh_id": 1,
                    "event_id": "event-1",
                    "event_title": "Test event",
                    "ordinal": 2,
                    "direction": "counterindicator",
                    "strength": "weak",
                    "relevance": 70,
                    "credibility": 70,
                    "title": "Processed candidate",
                    "source": "Source",
                    "url": "https://example.com/processed",
                    "published_at": None,
                    "summary": "Processed summary",
                    "why_it_matters": None,
                    "duplicate_risk": "low",
                    "freshness": "new",
                    "validation_state": "accepted",
                    "rejection_reason": None,
                    "created_at": "2026-09-20T09:00:00Z",
                    "decision_id": 5,
                    "decision_type": "keep_current",
                    "selected_probability": 35,
                    "decided_at": "2026-09-20T11:00:00Z",
                    "promotion_action": "inserted",
                    "evidence_id": 18,
                    "promoted_at": "2026-09-20T11:00:01Z",
                },
            ],
            "total": 2,
            "filtered_total": 2,
        }

    logged_in_client.app.state.prorok_api.list_candidate_evidence = list_candidate_evidence
    response = logged_in_client.get("/evidence?tab=candidates")

    assert response.status_code == 200
    assert "Pending candidate" in response.text
    assert "Processed candidate" not in response.text
    assert "Очікують рішення" in response.text
