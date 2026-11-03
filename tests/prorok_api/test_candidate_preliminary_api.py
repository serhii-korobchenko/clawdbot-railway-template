from __future__ import annotations

import sqlite3


def seed_candidate(conn, candidate_id, ordinal):
    conn.execute("""CREATE TABLE IF NOT EXISTS candidate_review_decisions (
        candidate_review_decision_id INTEGER PRIMARY KEY,
        candidate_id INTEGER NOT NULL,
        decision_type TEXT NOT NULL,
        decided_at TEXT NOT NULL
    )""")
    conn.execute("""
        INSERT INTO refresh_candidate_evidence
        (candidate_id,refresh_event_result_id,ordinal,direction,strength,
         relevance,credibility,title,source,url,summary,created_at,validation_state)
        VALUES(?,100,?,'indicator','medium',80,85,'Signal','Source',
               'https://example.org/source','Description','2026-11-03T12:00:00Z','accepted')
    """, (candidate_id, ordinal))


def test_candidate_list_preserves_global_id_without_preliminary_recommendation(client, db_path, auth_headers):
    with sqlite3.connect(db_path) as conn:
        seed_candidate(conn, 127, 2)
    response=client.get("/api/v1/evidence/candidates",headers=auth_headers)
    assert response.status_code == 200
    item=next(x for x in response.json()["items"] if x["candidate_id"] == 127)
    assert item["ordinal"] == 2
    assert item["preliminary_recommendation"] is None


def test_candidate_list_reports_latest_stored_recommendation_and_staleness(client, db_path, auth_headers):
    with sqlite3.connect(db_path) as conn:
        seed_candidate(conn, 127, 2)
        conn.executescript("""
            CREATE TABLE candidate_assessment_recommendations (
                candidate_assessment_recommendation_id INTEGER PRIMARY KEY,
                candidate_id INTEGER, event_id_snapshot TEXT, baseline_assessment_id INTEGER,
                baseline_probability INTEGER, recommended_probability INTEGER,
                probability_delta INTEGER, recommendation_confidence TEXT,
                recommendation_rationale TEXT, delta_justification TEXT,
                change_from_baseline TEXT, created_at TEXT, status TEXT
            );
            INSERT INTO candidate_assessment_recommendations
            VALUES(8,127,'active_event',1,35,40,5,'medium','Older rationale',
                   'Older change','increase','2026-11-02T12:00:00Z','ready');
            INSERT INTO candidate_assessment_recommendations
            VALUES(9,127,'active_event',1,35,45,10,'high','Latest rationale',
                   'Latest change','increase','2026-11-03T12:00:00Z','ready');
        """)
    response=client.get("/api/v1/evidence/candidates",headers=auth_headers)
    assert response.status_code == 200, response.text
    rec=next(x for x in response.json()["items"] if x["candidate_id"] == 127)["preliminary_recommendation"]
    assert rec["candidate_assessment_recommendation_id"] == 9
    assert rec["probability_delta"] == 10
    assert rec["is_stale"] is False
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO assessments(assessment_id,event_id,assessed_at,probability_percent,
                probability_band,probability_label,confidence)
            VALUES(2,'active_event','2026-11-03T13:00:00Z',40,'40-50%','Revised','high')
        """)
    response=client.get("/api/v1/evidence/candidates",headers=auth_headers)
    assert response.status_code == 200, response.text
    rec=response.json()["items"][0]["preliminary_recommendation"]
    assert rec["is_stale"] is True
    assert rec["recommendation_rationale"] == "Latest rationale"
