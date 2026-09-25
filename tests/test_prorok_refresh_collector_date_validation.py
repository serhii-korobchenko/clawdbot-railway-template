from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from pathlib import Path

from prorok.prorok_refresh_collector import validate_candidates


def make_validation_db(path: Path, *, prior_state: str, prior_date: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            assessed_at TEXT NOT NULL
        );

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            baseline_assessment_id INTEGER
        );

        CREATE TABLE refresh_candidate_evidence(
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_event_result_id INTEGER NOT NULL,
            url TEXT,
            published_at TEXT,
            validation_state TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO assessments(assessment_id, event_id, assessed_at) VALUES(1, 'event_a', '2026-09-01T12:00:00Z')"
    )
    conn.execute(
        "INSERT INTO refresh_event_results(refresh_event_result_id, event_id, baseline_assessment_id) VALUES(1, 'event_a', 1)"
    )
    conn.execute(
        "INSERT INTO refresh_event_results(refresh_event_result_id, event_id, baseline_assessment_id) VALUES(2, 'event_a', 1)"
    )
    conn.execute(
        """
        INSERT INTO refresh_candidate_evidence(
            refresh_event_result_id, url, published_at, validation_state
        )
        VALUES(1, 'https://example.com/a', ?, ?)
        """,
        (prior_date, prior_state),
    )
    conn.commit()
    return conn


def validate_current(conn: sqlite3.Connection, published_at: str) -> dict[str, str | None]:
    row = conn.execute(
        "SELECT * FROM refresh_event_results WHERE refresh_event_result_id = 2"
    ).fetchone()
    candidate = SimpleNamespace(
        ordinal=1,
        published_at=published_at,
        url='https://example.com/a',
    )
    parsed = SimpleNamespace(candidates=[candidate])
    return validate_candidates(conn, row, parsed)[1]


def test_legacy_unvalidated_conflicting_date_does_not_block_candidate(tmp_path: Path) -> None:
    conn = make_validation_db(
        tmp_path / 'db.sqlite3',
        prior_state='legacy_unvalidated',
        prior_date='2026-09-05',
    )
    try:
        result = validate_current(conn, '2026-09-09')
        assert result['validation_state'] == 'accepted'
        assert result['rejection_reason'] is None
    finally:
        conn.close()


def test_previously_accepted_conflicting_date_blocks_candidate(tmp_path: Path) -> None:
    conn = make_validation_db(
        tmp_path / 'db.sqlite3',
        prior_state='accepted',
        prior_date='2026-09-05',
    )
    try:
        result = validate_current(conn, '2026-09-09')
        assert result['validation_state'] == 'rejected_date_conflict'
        assert '2026-09-05' in (result['rejection_reason'] or '')
    finally:
        conn.close()


def test_same_accepted_calendar_date_allows_candidate(tmp_path: Path) -> None:
    conn = make_validation_db(
        tmp_path / 'db.sqlite3',
        prior_state='accepted',
        prior_date='2026-09-09T06:00:00Z',
    )
    try:
        result = validate_current(conn, '2026-09-09')
        assert result['validation_state'] == 'accepted'
        assert result['rejection_reason'] is None
    finally:
        conn.close()
