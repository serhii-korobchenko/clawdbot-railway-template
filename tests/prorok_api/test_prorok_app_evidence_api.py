from __future__ import annotations

import sqlite3


def _install_v17_table(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE evidence_prorok_app_status_history (
            prorok_app_status_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id INTEGER NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('marked','unmarked')),
            source TEXT NOT NULL,
            actor TEXT,
            changed_at TEXT NOT NULL,
            FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id) ON DELETE RESTRICT
        );
    """)
    conn.commit()
    conn.close()


def _add_status(db_path, state, changed_at):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO evidence_prorok_app_status_history(
               evidence_id,state,source,actor,changed_at
           ) VALUES(1,?,'telegram','tester',?)""",
        (state, changed_at),
    )
    conn.commit()
    conn.close()


def test_prorok_app_defaults_to_unmarked_and_filters(client, auth_headers, db_path):
    _install_v17_table(db_path)

    response = client.get("/api/v1/evidence", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["prorok_app"] == {
        "state": "unmarked",
        "source": None,
        "actor": None,
        "changed_at": None,
    }

    assert client.get(
        "/api/v1/evidence?prorok_app=unmarked", headers=auth_headers
    ).json()["filtered_total"] == 1
    assert client.get(
        "/api/v1/evidence?prorok_app=marked", headers=auth_headers
    ).json()["filtered_total"] == 0


def test_prorok_app_latest_history_state_is_reversible(client, auth_headers, db_path):
    _install_v17_table(db_path)
    _add_status(db_path, "marked", "2026-09-22T10:00:00.000Z")

    marked = client.get("/api/v1/evidence", headers=auth_headers).json()["items"][0]
    assert marked["prorok_app"]["state"] == "marked"
    assert marked["prorok_app"]["source"] == "telegram"
    assert marked["prorok_app"]["actor"] == "tester"
    assert client.get(
        "/api/v1/evidence?prorok_app=marked", headers=auth_headers
    ).json()["filtered_total"] == 1

    _add_status(db_path, "unmarked", "2026-09-22T11:00:00.000Z")
    unmarked = client.get("/api/v1/evidence", headers=auth_headers).json()["items"][0]
    assert unmarked["prorok_app"]["state"] == "unmarked"
    assert unmarked["prorok_app"]["changed_at"] == "2026-09-22T11:00:00.000Z"
    assert client.get(
        "/api/v1/evidence?prorok_app=marked", headers=auth_headers
    ).json()["filtered_total"] == 0
    assert client.get(
        "/api/v1/evidence?prorok_app=unmarked", headers=auth_headers
    ).json()["filtered_total"] == 1


def test_prorok_app_invalid_filter_is_422(client, auth_headers, db_path):
    _install_v17_table(db_path)
    response = client.get(
        "/api/v1/evidence?prorok_app=maybe", headers=auth_headers
    )
    assert response.status_code == 422



def test_official_evidence_is_not_duplicated_by_reused_candidate_promotion(
    client, auth_headers, db_path
):
    _install_v17_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO refresh_event_results(
            refresh_event_result_id,refresh_id,event_id,event_title_snapshot,
            baseline_probability,job_state,created_at
        ) VALUES(101,51,'active_event','Nuclear test event',35,'completed',
                 '2026-06-03T12:00:00Z')"""
    )
    conn.executemany(
        """INSERT INTO refresh_user_decisions(
            decision_id,refresh_event_result_id,event_id_snapshot,decision_type,
            baseline_probability,selected_probability,decision_source,decided_at
        ) VALUES(?,?,?,?,?,?,?,?)""",
        [
            (1,100,'active_event','keep_current',35,35,'telegram','2026-06-02T13:00:00Z'),
            (2,101,'active_event','keep_current',35,35,'telegram','2026-06-03T13:00:00Z'),
        ],
    )
    conn.executemany(
        """INSERT INTO refresh_candidate_promotions(
            promotion_id,candidate_id,refresh_event_result_id,decision_id,
            evidence_id,run_id,promotion_action,promoted_at
        ) VALUES(?,?,?,?,?,?,?,?)""",
        [
            (1,1,100,1,1,11,'inserted','2026-06-02T13:00:00Z'),
            (2,2,101,2,1,11,'reused','2026-06-03T13:00:00Z'),
        ],
    )
    conn.commit()
    conn.close()

    response = client.get("/api/v1/evidence", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["filtered_total"] == 1
    assert [item["evidence_id"] for item in payload["items"]] == [1]
    assert payload["items"][0]["assessment"]["refresh_id"] == 50


def test_official_evidence_sort_uses_publication_date_and_nulls_last(
    client, auth_headers, db_path
):
    _install_v17_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE sources SET published_at = ? WHERE source_id = 1", ("2026-06-01T00:00:00Z",))
    conn.execute(
        """INSERT INTO sources(
            source_id,url,canonical_url,canonical_url_hash,title,domain,published_at,
            first_seen_at,last_seen_at,source_type,raw_metadata
        ) VALUES(2,'https://example.com/b','https://example.com/b','hash2',
                 'Newer source','example.com','2026-06-05T00:00:00Z',
                 '2026-06-06T00:00:00Z','2026-06-06T00:00:00Z','web','{}')"""
    )
    conn.execute(
        """INSERT INTO sources(
            source_id,url,canonical_url,canonical_url_hash,title,domain,published_at,
            first_seen_at,last_seen_at,source_type,raw_metadata
        ) VALUES(3,'https://example.com/c','https://example.com/c','hash3',
                 'Unknown date','example.com',NULL,
                 '2026-06-07T00:00:00Z','2026-06-07T00:00:00Z','web','{}')"""
    )
    conn.executemany(
        """INSERT INTO evidence_items(
            evidence_id,event_id,source_id,run_id,direction,strength,summary,
            relevance,credibility,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [
            (2,'active_event',2,11,'indicator','medium','Newer publication',80,90,'2026-06-01T08:00:00Z'),
            (3,'active_event',3,11,'indicator','medium','No publication date',80,90,'2026-06-10T08:00:00Z'),
        ],
    )
    conn.commit()
    conn.close()

    newest = client.get("/api/v1/evidence?sort=newest", headers=auth_headers).json()["items"]
    oldest = client.get("/api/v1/evidence?sort=oldest", headers=auth_headers).json()["items"]

    assert [item["evidence_id"] for item in newest] == [2, 1, 3]
    assert [item["evidence_id"] for item in oldest] == [1, 2, 3]
