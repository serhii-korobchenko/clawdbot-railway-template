#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v1 to v2 refresh audit tables.

This migration is idempotent. It only creates the new refresh audit tables and
indexes, then updates meta.schema_version to 2. It does not modify existing
events, assessments, evidence, sources, or historical runs.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"

MIGRATION_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS refresh_runs (
    refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    mode TEXT NOT NULL DEFAULT 'dry_run'
        CHECK(mode IN ('dry_run', 'apply')),
    trigger_source TEXT NOT NULL
        CHECK(trigger_source IN ('scheduled', 'telegram', 'manual_cli', 'system')),
    scope TEXT NOT NULL DEFAULT 'all'
        CHECK(scope IN ('all', 'event')),
    target_event_id TEXT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running', 'completed', 'failed', 'partial')),
    events_checked INTEGER NOT NULL DEFAULT 0 CHECK(events_checked >= 0),
    events_with_new_evidence INTEGER NOT NULL DEFAULT 0 CHECK(events_with_new_evidence >= 0),
    new_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(new_evidence_count >= 0),
    recommendations_count INTEGER NOT NULL DEFAULT 0 CHECK(recommendations_count >= 0),
    no_change_count INTEGER NOT NULL DEFAULT 0 CHECK(no_change_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
    model_used TEXT,
    summary TEXT,
    errors TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL,
    FOREIGN KEY(target_event_id) REFERENCES events(event_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS refresh_event_results (
    refresh_event_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_id INTEGER NOT NULL,
    event_id TEXT,
    event_title_snapshot TEXT NOT NULL,
    baseline_assessment_id INTEGER,
    baseline_probability INTEGER CHECK(baseline_probability BETWEEN 0 AND 100),
    outcome TEXT NOT NULL
        CHECK(outcome IN ('new_evidence', 'no_new_evidence', 'error', 'skipped')),
    new_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(new_evidence_count >= 0),
    indicator_count INTEGER NOT NULL DEFAULT 0 CHECK(indicator_count >= 0),
    counterindicator_count INTEGER NOT NULL DEFAULT 0 CHECK(counterindicator_count >= 0),
    recommended_probability INTEGER CHECK(recommended_probability BETWEEN 0 AND 100),
    recommendation_confidence TEXT
        CHECK(recommendation_confidence IN ('low', 'medium', 'high')),
    change_recommended INTEGER NOT NULL DEFAULT 0 CHECK(change_recommended IN (0, 1)),
    recommendation_reason TEXT,
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(refresh_id) REFERENCES refresh_runs(refresh_id) ON DELETE CASCADE,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE SET NULL,
    FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id) ON DELETE SET NULL,
    UNIQUE(refresh_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_finished
ON refresh_runs(finished_at DESC, refresh_id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_status
ON refresh_runs(status);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_refresh
ON refresh_event_results(refresh_id);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_event
ON refresh_event_results(event_id, refresh_id DESC);

INSERT INTO meta(key, value)
VALUES ('schema_version', '2')
ON CONFLICT(key) DO UPDATE SET
    value = excluded.value,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
"""


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PROROK schema migration v1 -> v2")
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate current state without changing the database",
    )
    args = parser.parse_args()

    db = resolve_db(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        before = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        before_version = before["value"] if before else None

        if args.check_only:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            print("PROROK migration v2 check")
            print(f"db: {db}")
            print(f"schema_version: {before_version}")
            print(f"refresh_runs: {'yes' if 'refresh_runs' in tables else 'no'}")
            print(
                "refresh_event_results: "
                + ("yes" if "refresh_event_results" in tables else "no")
            )
            return 0

        conn.executescript(MIGRATION_SQL)
        conn.commit()

        after = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

        if "refresh_runs" not in tables or "refresh_event_results" not in tables:
            raise RuntimeError("refresh audit tables were not created")
        if not after or after["value"] != "2":
            raise RuntimeError("meta.schema_version was not updated to 2")
        if fk_errors:
            raise RuntimeError(f"foreign_key_check returned {len(fk_errors)} error(s)")

        print("OK: PROROK schema migration v2 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print("schema_version_after: 2")
        print("refresh_runs: yes")
        print("refresh_event_results: yes")
        print("foreign_key_check: ok")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
