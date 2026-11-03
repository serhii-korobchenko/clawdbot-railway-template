from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _add_recent(db_path):
    created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO evidence_items(
            evidence_id, event_id, source_id, run_id, direction, strength,
            summary, relevance, credibility, created_at
        ) VALUES (2, 'active_event', 1, 12, 'counterindicator', 'strong',
                  'Recent counter evidence', 90, 90, ?)""",
        (created_at,),
    )
    conn.commit()
    conn.close()
    return created_at


def test_activity_all_time_and_zero_events(client, auth_headers):
    payload = client.get(
        "/api/v1/evidence/activity?status=active&window=all",
        headers=auth_headers,
    ).json()
    assert payload["window"] == "all"
    assert payload["cutoff_at"] is None
    assert payload["total_evidence"] == 1
    assert payload["items"][0]["indicator_count"] == 1

    archived = client.get(
        "/api/v1/evidence/activity?status=archived&window=all",
        headers=auth_headers,
    ).json()["items"][0]
    assert archived["evidence_count"] == 0
    assert archived["latest_evidence_at"] is None


def test_activity_7d_uses_created_at(client, auth_headers, db_path):
    created_at = _add_recent(db_path)
    payload = client.get(
        "/api/v1/evidence/activity?status=active&window=7d",
        headers=auth_headers,
    ).json()
    item = payload["items"][0]
    assert payload["total_evidence"] == 1
    assert item["indicator_count"] == 0
    assert item["counterindicator_count"] == 1
    assert item["latest_evidence_at"].startswith(created_at[:19])

    all_time = client.get(
        "/api/v1/evidence/activity?status=active&window=all",
        headers=auth_headers,
    ).json()["items"][0]
    assert all_time["evidence_count"] == 2


def test_activity_invalid_window_is_422(client, auth_headers):
    assert client.get(
        "/api/v1/evidence/activity?window=365d",
        headers=auth_headers,
    ).status_code == 422
