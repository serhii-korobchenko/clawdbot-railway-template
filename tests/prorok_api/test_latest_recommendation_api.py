import sqlite3


def test_latest_recommendation_is_actionable(client, auth_headers):
    response = client.get(
        "/api/v1/events/active_event/latest-recommendation",
        headers=auth_headers,
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["event_id"] == "active_event"

    rec = payload["recommendation"]
    assert rec["refresh_event_result_id"] == 100
    assert rec["baseline_assessment_id"] == 1
    assert rec["baseline_probability"] == 35
    assert rec["recommended_probability"] == 45
    assert rec["current_assessment_id"] == 1
    assert rec["current_probability"] == 35
    assert rec["recommendation_valid"] is True
    assert rec["change_recommended"] is True
    assert rec["is_stale"] is False
    assert rec["actionable"] is True
    assert rec["status"] == "actionable"
    assert rec["decision"] is None


def test_latest_recommendation_becomes_decided(
    client,
    auth_headers,
    db_path,
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO refresh_user_decisions(
            decision_id,
            refresh_event_result_id,
            event_id_snapshot,
            decision_type,
            baseline_assessment_id,
            baseline_probability,
            recommended_probability,
            selected_probability,
            assessment_id,
            decision_source,
            decided_at
        ) VALUES (
            7,
            100,
            'active_event',
            'keep_current',
            1,
            35,
            45,
            35,
            NULL,
            'telegram',
            '2026-06-02T13:00:00Z'
        )
        """
    )
    conn.commit()
    conn.close()

    response = client.get(
        "/api/v1/events/active_event/latest-recommendation",
        headers=auth_headers,
    )
    assert response.status_code == 200

    rec = response.json()["recommendation"]
    assert rec["status"] == "decided"
    assert rec["actionable"] is False
    assert rec["decision"]["decision_id"] == 7
    assert rec["decision"]["decision_type"] == "keep_current"
    assert rec["decision"]["selected_probability"] == 35
    assert rec["decision"]["assessment_id"] is None


def test_latest_recommendation_detects_stale_baseline(
    client,
    auth_headers,
    db_path,
):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO assessments(
            assessment_id, event_id, run_id, assessed_at,
            probability_percent, probability_band, probability_label,
            confidence, delta_from_previous, rationale
        ) VALUES (
            2, 'active_event', 12, '2026-06-03T10:00:00Z',
            40, '40-50%', 'Реалістична можливість',
            'medium', 5, 'Later manual assessment.'
        )
        """
    )
    conn.commit()
    conn.close()

    response = client.get(
        "/api/v1/events/active_event/latest-recommendation",
        headers=auth_headers,
    )
    assert response.status_code == 200

    rec = response.json()["recommendation"]
    assert rec["current_assessment_id"] == 2
    assert rec["current_probability"] == 40
    assert rec["is_stale"] is True
    assert rec["actionable"] is False
    assert rec["status"] == "stale"


def test_latest_recommendation_none_and_unknown(client, auth_headers):
    response = client.get(
        "/api/v1/events/archived_empty/latest-recommendation",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == {
        "event_id": "archived_empty",
        "recommendation": None,
    }

    missing = client.get(
        "/api/v1/events/not-there/latest-recommendation",
        headers=auth_headers,
    )
    assert missing.status_code == 404


def test_latest_recommendation_requires_auth(client):
    response = client.get(
        "/api/v1/events/active_event/latest-recommendation"
    )
    assert response.status_code == 401
