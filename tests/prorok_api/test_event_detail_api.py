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
    assert payload["limitations"]["assessment_evidence_attribution"] == "refresh_and_manual_provenance"
    assert payload["evidence"][0]["assessment"]["status"] == "unknown"


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


def test_manual_evidence_assessment_provenance_overrides_unknown(client, auth_headers, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO assessments(assessment_id,event_id,run_id,assessed_at,probability_percent,probability_band,
           probability_label,confidence,delta_from_previous,rationale)
           VALUES(2,'active_event',12,'2026-06-03T10:00:00Z',35,'25-35%','Малоймовірно','medium',0,'Manual evidence decision.')"""
    )
    conn.execute(
        """INSERT INTO evidence_assessment_decisions(
           evidence_assessment_decision_id,event_id_snapshot,evidence_id,baseline_assessment_id,
           baseline_probability,selected_probability,assessment_id,run_id,decision_source,actor,decided_at)
           VALUES(1,'active_event',1,1,35,35,2,12,'telegram','test','2026-06-03T10:00:00Z')"""
    )
    conn.commit(); conn.close()
    payload=client.get("/api/v1/events/active_event",headers=auth_headers).json()
    assessment=payload["evidence"][0]["assessment"]
    assert assessment["status"] == "assessed_unchanged"
    assert assessment["provenance_type"] == "evidence_manual"
    assert assessment["evidence_assessment_decision_id"] == 1
    assert assessment["assessment_id"] == 2
    assert assessment["baseline_probability"] == 35
    assert assessment["selected_probability"] == 35


def test_detail_does_not_duplicate_evidence_for_reused_promotions(client, auth_headers, db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executemany(
        """INSERT INTO refresh_candidate_promotions(
           promotion_id,candidate_id,refresh_event_result_id,decision_id,evidence_id,run_id,promotion_action,promoted_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        [
            (1,101,100,201,1,11,"inserted","2026-06-02T12:00:00Z"),
            (2,102,100,202,1,12,"reused","2026-06-03T12:00:00Z"),
            (3,103,100,203,1,13,"reused","2026-06-04T12:00:00Z"),
        ],
    )
    conn.commit(); conn.close()
    payload = client.get("/api/v1/events/active_event", headers=auth_headers).json()
    assert [item["evidence_id"] for item in payload["evidence"]] == [1]
