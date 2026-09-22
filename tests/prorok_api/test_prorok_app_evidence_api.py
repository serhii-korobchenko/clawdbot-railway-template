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
