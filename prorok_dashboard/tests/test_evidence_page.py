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
                }
            ],
            "total": 5,
            "filtered_total": 1,
        }

    logged_in_client.app.state.prorok_api.list_candidate_evidence = list_candidate_evidence
    response = logged_in_client.get(
        "/evidence?tab=candidates&validation_state=rejected_source_policy&sort=oldest"
    )

    assert response.status_code == 200
    assert "Candidate evidence" in response.text
    assert "Rejected candidate" in response.text
    assert "rejected_source_policy" in response.text
    assert "domain is banned" in response.text
    assert "Refresh #24" in response.text
    assert "Старі спочатку" in response.text


def test_evidence_requires_authentication(client):
    response = client.get("/evidence", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
