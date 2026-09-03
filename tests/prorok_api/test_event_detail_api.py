def test_detail_includes_history_evidence_and_limitations(client, auth_headers):
    response = client.get(
        "/api/v1/events/active_event",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["current_assessment"]["probability_percent"] == 35
    assert payload["assessments"][0]["rationale"] == "Because."
    assert payload["evidence"][0]["direction"] == "indicator"
    assert payload["evidence"][0]["source"]["published_at"] is None
    assert payload["limitations"]["assessment_evidence_attribution"] == "unavailable"


def test_legacy_event_and_unknown(client, auth_headers):
    archived = client.get(
        "/api/v1/events/archived_empty",
        headers=auth_headers,
    ).json()
    assert archived["event"]["decision_criteria"]["format"] == "text"
    assert archived["current_assessment"] is None
    assert archived["evidence"] == []

    assert client.get(
        "/api/v1/events/not-there",
        headers=auth_headers,
    ).status_code == 404


def test_no_mutating_api_routes(client):
    methods = set()
    for route in client.app.routes:
        if getattr(route, "path", "").startswith("/api/v1/"):
            methods.update(getattr(route, "methods", set()))

    assert not (methods & {"POST", "PUT", "PATCH", "DELETE"})
