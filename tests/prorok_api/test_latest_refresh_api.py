import sqlite3


def test_latest_refresh_returns_run_results_and_decision(
    client,
    auth_headers,
    db_path,
):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE refresh_runs (
            refresh_id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            scope TEXT NOT NULL,
            target_event_id TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            events_checked INTEGER NOT NULL DEFAULT 0,
            events_with_new_evidence INTEGER NOT NULL DEFAULT 0,
            new_evidence_count INTEGER NOT NULL DEFAULT 0,
            recommendations_count INTEGER NOT NULL DEFAULT 0,
            no_change_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            phase TEXT NOT NULL,
            collector_version TEXT
        );

        ALTER TABLE refresh_event_results
            ADD COLUMN new_evidence_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE refresh_event_results
            ADD COLUMN indicator_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE refresh_event_results
            ADD COLUMN counterindicator_count INTEGER NOT NULL DEFAULT 0;

        INSERT INTO refresh_runs(
            refresh_id, mode, trigger_source, scope, target_event_id,
            started_at, finished_at, status,
            events_checked, events_with_new_evidence, new_evidence_count,
            recommendations_count, no_change_count, error_count,
            phase, collector_version
        ) VALUES (
            50, 'dry_run', 'manual_cli', 'all', NULL,
            '2026-06-02T11:55:00Z', '2026-06-02T12:05:00Z', 'completed',
            1, 1, 3, 1, 0, 0,
            'done', '3'
        );

        UPDATE refresh_event_results
        SET new_evidence_count = 3,
            indicator_count = 3,
            counterindicator_count = 0
        WHERE refresh_event_result_id = 100;

        INSERT INTO assessments(
            assessment_id, event_id, run_id, assessed_at,
            probability_percent, probability_band, probability_label,
            confidence, delta_from_previous, rationale
        ) VALUES (
            2, 'active_event', 12, '2026-06-03T10:00:00Z',
            45, '40-50%', 'Реалістична можливість',
            'medium', 10, 'Accepted refresh recommendation.'
        );

        INSERT INTO refresh_user_decisions(
            decision_id, refresh_event_result_id, event_id_snapshot,
            decision_type, baseline_assessment_id, baseline_probability,
            recommended_probability, selected_probability, assessment_id,
            decision_source, decided_at
        ) VALUES (
            1, 100, 'active_event',
            'accept_recommendation', 1, 35,
            45, 45, 2,
            'telegram', '2026-06-03T10:00:00Z'
        );
        """
    )
    conn.commit()
    conn.close()

    response = client.get("/api/v1/refresh/latest", headers=auth_headers)
    assert response.status_code == 200

    payload = response.json()
    assert payload["refresh"]["refresh_id"] == 50
    assert payload["refresh"]["status"] == "completed"
    assert payload["refresh"]["new_evidence_count"] == 3
    assert payload["refresh"]["recommendations_count"] == 1
    assert payload["refresh"]["collector_version"] == "3"

    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["refresh_event_result_id"] == 100
    assert result["new_evidence_count"] == 3
    assert result["indicator_count"] == 3
    assert result["counterindicator_count"] == 0
    assert result["baseline_probability"] == 35
    assert result["recommended_probability"] == 45
    assert result["current_probability"] == 45
    assert result["decision"]["decision_type"] == "accept_recommendation"
    assert result["decision"]["decision_source"] == "telegram"
    assert result["decision"]["selected_probability"] == 45
