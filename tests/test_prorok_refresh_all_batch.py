from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from prorok.prorok_refresh_all_dry_run_quiet import (
    RefreshTarget,
    create_refresh_batch,
    finalize_scheduling,
    mark_schedule_result,
)


def make_v3_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT INTO meta(key, value) VALUES('schema_version', '3');

        CREATE TABLE events(
            event_id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            forecast_horizon TEXT,
            updated_at TEXT
        );

        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT,
            assessed_at TEXT,
            probability_percent INTEGER
        );

        CREATE TABLE runs(
            run_id INTEGER PRIMARY KEY
        );

        CREATE TABLE refresh_runs(
            refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            mode TEXT NOT NULL DEFAULT 'dry_run',
            trigger_source TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'all',
            target_event_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            events_checked INTEGER NOT NULL DEFAULT 0,
            events_with_new_evidence INTEGER NOT NULL DEFAULT 0,
            new_evidence_count INTEGER NOT NULL DEFAULT 0,
            recommendations_count INTEGER NOT NULL DEFAULT 0,
            no_change_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            model_used TEXT,
            summary TEXT,
            errors TEXT,
            phase TEXT NOT NULL DEFAULT 'scheduling',
            target_count INTEGER NOT NULL DEFAULT 0,
            scheduled_count INTEGER NOT NULL DEFAULT 0,
            scheduling_finished_at TEXT,
            collection_started_at TEXT,
            last_collected_at TEXT,
            collector_version TEXT
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
            cron_id TEXT,
            expected_run_at TEXT,
            cron_run_at_ms INTEGER,
            session_id TEXT,
            session_key TEXT,
            source_run_key TEXT UNIQUE,
            cron_summary_preview TEXT,
            transcript_raw TEXT,
            transcript_sha256 TEXT,
            parser_version TEXT,
            parse_error TEXT,
            collected_at TEXT,
            cron_status TEXT,
            duration_ms INTEGER,
            model TEXT,
            provider TEXT,
            delivered INTEGER,
            delivery_status TEXT,
            search_window TEXT,
            no_evidence_reason TEXT,
            recommended_band TEXT,
            recommended_label TEXT,
            change_from_baseline TEXT,
            do_not_write INTEGER,
            next_step TEXT,
            new_evidence_count INTEGER NOT NULL DEFAULT 0,
            indicator_count INTEGER NOT NULL DEFAULT 0,
            counterindicator_count INTEGER NOT NULL DEFAULT 0,
            recommended_probability INTEGER,
            recommendation_confidence TEXT,
            change_recommended INTEGER NOT NULL DEFAULT 0,
            recommendation_reason TEXT,
            summary TEXT,
            created_at TEXT,
            UNIQUE(refresh_id, event_id)
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

    conn.executemany(
        """
        INSERT INTO events(event_id,title,status,forecast_horizon,updated_at)
        VALUES(?,?,?,?,?)
        """,
        [
            ("event_a", "Event A", "active", "2026-12-31", "2026-09-08T10:00:00Z"),
            ("event_b", "Event B", "active", "2026-12-31", "2026-09-08T09:00:00Z"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO assessments(assessment_id,event_id,assessed_at,probability_percent)
        VALUES(?,?,?,?)
        """,
        [
            (1, "event_a", "2026-09-01T00:00:00Z", 20),
            (2, "event_a", "2026-09-07T00:00:00Z", 30),
            (3, "event_b", "2026-09-06T00:00:00Z", 40),
        ],
    )
    conn.commit()
    conn.close()


def targets() -> list[RefreshTarget]:
    return [
        RefreshTarget(
            event_id="event_a",
            title="Event A",
            status="active",
            forecast_horizon="2026-12-31",
            updated_at="2026-09-08T10:00:00Z",
            baseline_assessment_id=2,
            baseline_probability=30,
        ),
        RefreshTarget(
            event_id="event_b",
            title="Event B",
            status="active",
            forecast_horizon="2026-12-31",
            updated_at="2026-09-08T09:00:00Z",
            baseline_assessment_id=3,
            baseline_probability=40,
        ),
    ]


def fetch_rows(db: Path):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    batch = conn.execute("SELECT * FROM refresh_runs").fetchone()
    children = conn.execute(
        "SELECT * FROM refresh_event_results ORDER BY refresh_event_result_id"
    ).fetchall()
    conn.close()
    return batch, children


def test_create_refresh_batch_snapshots_targets(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)

    refresh_id, child_ids = create_refresh_batch(db, targets(), "manual_cli")
    batch, children = fetch_rows(db)

    assert refresh_id == batch["refresh_id"]
    assert batch["status"] == "running"
    assert batch["phase"] == "scheduling"
    assert batch["target_count"] == 2
    assert batch["scheduled_count"] == 0
    assert batch["trigger_source"] == "manual_cli"

    assert set(child_ids) == {"event_a", "event_b"}
    assert [row["job_state"] for row in children] == ["pending", "pending"]
    assert [row["outcome"] for row in children] == [None, None]
    assert [row["baseline_assessment_id"] for row in children] == [2, 3]
    assert [row["baseline_probability"] for row in children] == [30, 40]


def test_mark_schedule_success_and_failure_then_finalize_waiting(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)

    refresh_id, child_ids = create_refresh_batch(db, targets(), "telegram")

    mark_schedule_result(
        db,
        refresh_id,
        child_ids["event_a"],
        scheduled=True,
        cron_id="cron-a",
        run_at="2026-09-08T20:00:00Z",
    )
    mark_schedule_result(
        db,
        refresh_id,
        child_ids["event_b"],
        scheduled=False,
        error="scheduler failed",
    )

    scheduled_count, failed_count = finalize_scheduling(db, refresh_id)
    batch, children = fetch_rows(db)

    assert scheduled_count == 1
    assert failed_count == 1
    assert batch["status"] == "running"
    assert batch["phase"] == "waiting"
    assert batch["scheduled_count"] == 1
    assert batch["error_count"] == 1
    assert batch["finished_at"] is None

    assert children[0]["job_state"] == "scheduled"
    assert children[0]["cron_id"] == "cron-a"
    assert children[0]["expected_run_at"] == "2026-09-08T20:00:00Z"
    assert children[0]["outcome"] is None

    assert children[1]["job_state"] == "schedule_failed"
    assert children[1]["outcome"] == "error"
    assert children[1]["parse_error"] == "scheduler failed"


def test_finalize_all_schedule_failed_marks_batch_failed_done(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)

    refresh_id, child_ids = create_refresh_batch(db, targets(), "scheduled")

    for event_id in ("event_a", "event_b"):
        mark_schedule_result(
            db,
            refresh_id,
            child_ids[event_id],
            scheduled=False,
            error=f"failed {event_id}",
        )

    scheduled_count, failed_count = finalize_scheduling(db, refresh_id)
    batch, _ = fetch_rows(db)

    assert scheduled_count == 0
    assert failed_count == 2
    assert batch["status"] == "failed"
    assert batch["phase"] == "done"
    assert batch["scheduled_count"] == 0
    assert batch["error_count"] == 2
    assert batch["finished_at"] is not None


def test_require_v3_schema_fails_on_wrong_version(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v3_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    try:
        create_refresh_batch(db, targets(), "manual_cli")
    except RuntimeError as exc:
        assert "schema v3 required" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for non-v3 schema")
