from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def load_migration_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "prorok"
        / "migrations"
        / "004_candidate_quarantine.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prorok_migration_004_candidate_quarantine",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v3_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );

        INSERT INTO meta(key, value)
        VALUES('schema_version', '3');

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_id INTEGER NOT NULL,
            event_id TEXT,
            job_state TEXT NOT NULL DEFAULT 'pending',
            outcome TEXT
        );

        CREATE TABLE refresh_candidate_evidence(
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_event_result_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            direction TEXT NOT NULL,
            title TEXT,
            url TEXT,
            published_at TEXT,
            freshness TEXT,
            UNIQUE(refresh_event_result_id, ordinal)
        );

        INSERT INTO refresh_event_results(
            refresh_id, event_id, job_state, outcome
        )
        VALUES(1, 'event_a', 'completed', 'new_evidence');

        INSERT INTO refresh_candidate_evidence(
            refresh_event_result_id,
            ordinal,
            direction,
            title,
            url,
            published_at,
            freshness
        )
        VALUES(
            1,
            1,
            'indicator',
            'Historical candidate',
            'https://example.com/a',
            '2026-09-01',
            'new_after_last_assessment'
        );
        """
    )
    conn.commit()
    conn.close()


def test_migration_v4_preserves_historical_candidate_as_legacy(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)
    migration = load_migration_module()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    migration.apply_migration(conn)
    conn.commit()

    assert migration.schema_version(conn) == "4"

    candidate = conn.execute(
        """
        SELECT validation_state, rejection_reason
        FROM refresh_candidate_evidence
        WHERE candidate_id = 1
        """
    ).fetchone()

    assert candidate["validation_state"] == "legacy_unvalidated"
    assert candidate["rejection_reason"] is None

    result = conn.execute(
        """
        SELECT candidate_rejected_count, recommendation_valid
        FROM refresh_event_results
        WHERE refresh_event_result_id = 1
        """
    ).fetchone()

    assert result["candidate_rejected_count"] == 0
    assert result["recommendation_valid"] == 1
    assert migration.validate(conn) == []
    conn.close()


def test_migration_v4_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)
    migration = load_migration_module()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    migration.apply_migration(conn)
    migration.apply_migration(conn)
    conn.commit()

    candidate_count = conn.execute(
        "SELECT COUNT(*) AS n FROM refresh_candidate_evidence"
    ).fetchone()["n"]

    assert candidate_count == 1
    assert migration.schema_version(conn) == "4"
    assert migration.validate(conn) == []
    conn.close()
