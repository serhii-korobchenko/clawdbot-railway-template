from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "prorok"
    / "migrations"
    / "004_candidate_quarantine.py"
)


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "prorok_migration_004_candidate_quarantine",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_v3_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE events(
            event_id TEXT PRIMARY KEY
        );

        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            assessed_at TEXT NOT NULL
        );

        CREATE TABLE refresh_runs(
            refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_source TEXT NOT NULL,
            status TEXT NOT NULL,
            phase TEXT NOT NULL
        );

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_id INTEGER NOT NULL,
            event_id TEXT,
            event_title_snapshot TEXT NOT NULL,
            baseline_assessment_id INTEGER,
            baseline_probability INTEGER,
            job_state TEXT NOT NULL DEFAULT 'pending',
            outcome TEXT,
            recommended_probability INTEGER,
            recommended_band TEXT,
            recommendation_confidence TEXT,
            change_from_baseline TEXT,
            change_recommended INTEGER NOT NULL DEFAULT 0,
            new_evidence_count INTEGER NOT NULL DEFAULT 0,
            indicator_count INTEGER NOT NULL DEFAULT 0,
            counterindicator_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE refresh_candidate_evidence(
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_event_result_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            direction TEXT NOT NULL,
            strength TEXT,
            relevance INTEGER,
            credibility INTEGER,
            title TEXT,
            source TEXT,
            url TEXT,
            published_at TEXT,
            summary TEXT,
            why_it_matters TEXT,
            duplicate_risk TEXT,
            freshness TEXT,
            created_at TEXT,
            UNIQUE(refresh_event_result_id, ordinal)
        );
        """
    )

    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', '3')"
    )
    conn.execute("INSERT INTO events(event_id) VALUES('event_a')")
    conn.execute(
        """
        INSERT INTO assessments(assessment_id, event_id, assessed_at)
        VALUES(1, 'event_a', '2026-09-01T11:56:19Z')
        """
    )
    refresh_id = conn.execute(
        """
        INSERT INTO refresh_runs(trigger_source, status, phase)
        VALUES('manual_cli', 'completed', 'done')
        """
    ).lastrowid
    result_id = conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_id,
            event_id,
            event_title_snapshot,
            baseline_assessment_id,
            baseline_probability,
            job_state,
            outcome
        )
        VALUES(?, 'event_a', 'Event A', 1, 20, 'completed', 'new_evidence')
        """,
        (refresh_id,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO refresh_candidate_evidence(
            refresh_event_result_id,
            ordinal,
            direction,
            title,
            url,
            published_at,
            freshness
        )
        VALUES(?, 1, 'indicator', 'Historical', 'https://example.com/a',
               '2026-09-05', 'new_after_last_assessment')
        """,
        (result_id,),
    )

    conn.commit()
    conn.close()


def test_migration_v4_preserves_history_and_marks_legacy_unvalidated(
    tmp_path: Path,
) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)
    migration = load_migration()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    migration.apply_migration(conn)
    conn.commit()

    version = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()["value"]
    assert version == "4"

    candidate = conn.execute(
        """
        SELECT validation_state, rejection_reason
        FROM refresh_candidate_evidence
        """
    ).fetchone()
    assert candidate["validation_state"] == "legacy_unvalidated"
    assert candidate["rejection_reason"] is None

    result = conn.execute(
        """
        SELECT candidate_rejected_count, recommendation_valid
        FROM refresh_event_results
        """
    ).fetchone()
    assert result["candidate_rejected_count"] == 0
    assert result["recommendation_valid"] == 0

    assert migration.validate(conn) == []

    migration.apply_migration(conn)
    conn.commit()
    assert migration.validate(conn) == []

    candidate_count = conn.execute(
        "SELECT COUNT(*) AS n FROM refresh_candidate_evidence"
    ).fetchone()["n"]
    assert candidate_count == 1

    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
