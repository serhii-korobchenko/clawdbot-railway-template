#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v2 to v3 refresh collector lifecycle.

v3 prepares deterministic collection of OpenClaw refresh results. It:
- extends refresh_runs with batch lifecycle / collector metadata;
- rebuilds refresh_event_results so outcome may remain NULL while a job is pending;
- adds deterministic cron/session correlation and parser/audit fields;
- creates refresh_candidate_evidence for dry-run candidates.

The migration preserves existing v2 refresh rows. It does not create or modify
official assessments, evidence_items, sources, events, or historical manual runs.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "3"


REFRESH_RUNS_COLUMNS = {
    "phase": "TEXT NOT NULL DEFAULT 'scheduling' CHECK(phase IN ('scheduling', 'waiting', 'collecting', 'finalizing', 'done'))",
    "target_count": "INTEGER NOT NULL DEFAULT 0 CHECK(target_count >= 0)",
    "scheduled_count": "INTEGER NOT NULL DEFAULT 0 CHECK(scheduled_count >= 0)",
    "scheduling_finished_at": "TEXT",
    "collection_started_at": "TEXT",
    "last_collected_at": "TEXT",
    "collector_version": "TEXT",
}


REFRESH_EVENT_RESULTS_V3_SQL = """
CREATE TABLE refresh_event_results_v3 (
    refresh_event_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_id INTEGER NOT NULL,
    event_id TEXT,
    event_title_snapshot TEXT NOT NULL,
    baseline_assessment_id INTEGER,
    baseline_probability INTEGER CHECK(baseline_probability BETWEEN 0 AND 100),

    job_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(job_state IN (
            'pending',
            'scheduled',
            'completed',
            'schedule_failed',
            'execution_failed',
            'timeout',
            'source_missing',
            'parse_failed',
            'encoding_failed'
        )),

    outcome TEXT
        CHECK(outcome IS NULL OR outcome IN (
            'new_evidence',
            'no_new_evidence',
            'error',
            'skipped'
        )),

    cron_id TEXT,
    expected_run_at TEXT,
    cron_run_at_ms INTEGER CHECK(cron_run_at_ms IS NULL OR cron_run_at_ms >= 0),
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
    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
    model TEXT,
    provider TEXT,
    delivered INTEGER CHECK(delivered IS NULL OR delivered IN (0, 1)),
    delivery_status TEXT,

    search_window TEXT,
    no_evidence_reason TEXT,
    recommended_band TEXT,
    recommended_label TEXT,
    change_from_baseline TEXT
        CHECK(change_from_baseline IS NULL OR change_from_baseline IN (
            'increase',
            'decrease',
            'no_update'
        )),
    do_not_write INTEGER CHECK(do_not_write IS NULL OR do_not_write IN (0, 1)),
    next_step TEXT,

    new_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(new_evidence_count >= 0),
    indicator_count INTEGER NOT NULL DEFAULT 0 CHECK(indicator_count >= 0),
    counterindicator_count INTEGER NOT NULL DEFAULT 0 CHECK(counterindicator_count >= 0),
    recommended_probability INTEGER CHECK(recommended_probability BETWEEN 0 AND 100),
    recommendation_confidence TEXT
        CHECK(recommendation_confidence IS NULL OR recommendation_confidence IN ('low', 'medium', 'high')),
    change_recommended INTEGER NOT NULL DEFAULT 0 CHECK(change_recommended IN (0, 1)),
    recommendation_reason TEXT,
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    FOREIGN KEY(refresh_id) REFERENCES refresh_runs(refresh_id) ON DELETE CASCADE,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE SET NULL,
    FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id) ON DELETE SET NULL,
    UNIQUE(refresh_id, event_id)
);
"""


CANDIDATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS refresh_candidate_evidence (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_event_result_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    direction TEXT NOT NULL CHECK(direction IN ('indicator', 'counterindicator')),
    strength TEXT CHECK(strength IS NULL OR strength IN ('weak', 'medium', 'strong')),
    relevance INTEGER CHECK(relevance IS NULL OR relevance BETWEEN 0 AND 100),
    credibility INTEGER CHECK(credibility IS NULL OR credibility BETWEEN 0 AND 100),
    title TEXT,
    source TEXT,
    url TEXT,
    published_at TEXT,
    summary TEXT,
    why_it_matters TEXT,
    duplicate_risk TEXT,
    freshness TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    FOREIGN KEY(refresh_event_result_id)
        REFERENCES refresh_event_results(refresh_event_result_id)
        ON DELETE CASCADE,
    UNIQUE(refresh_event_result_id, ordinal)
);
"""


INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_refresh_runs_finished
ON refresh_runs(finished_at DESC, refresh_id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_status
ON refresh_runs(status);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_phase
ON refresh_runs(phase, status, refresh_id);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_refresh
ON refresh_event_results(refresh_id);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_event
ON refresh_event_results(event_id, refresh_id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_job_state
ON refresh_event_results(job_state, refresh_id, refresh_event_result_id);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_cron
ON refresh_event_results(cron_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_refresh_event_results_source_run_key
ON refresh_event_results(source_run_key)
WHERE source_run_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_refresh_candidate_result
ON refresh_candidate_evidence(refresh_event_result_id, ordinal);
"""


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).resolve()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {
        row["name"]: row
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return row["value"] if row else None


def add_refresh_run_columns(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "refresh_runs")
    for name, definition in REFRESH_RUNS_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE refresh_runs ADD COLUMN {name} {definition}")

    # Existing terminal batches should remain terminal after migration.
    conn.execute(
        """
        UPDATE refresh_runs
        SET phase = CASE
            WHEN status IN ('completed', 'failed', 'partial') THEN 'done'
            WHEN phase IS NULL OR phase = '' THEN 'waiting'
            ELSE phase
        END
        """
    )


def needs_result_rebuild(conn: sqlite3.Connection) -> bool:
    columns = table_columns(conn, "refresh_event_results")
    required = {
        "job_state",
        "cron_id",
        "expected_run_at",
        "cron_run_at_ms",
        "session_id",
        "session_key",
        "source_run_key",
        "cron_summary_preview",
        "transcript_raw",
        "transcript_sha256",
        "parser_version",
        "parse_error",
        "collected_at",
        "cron_status",
        "duration_ms",
        "model",
        "provider",
        "delivered",
        "delivery_status",
        "search_window",
        "no_evidence_reason",
        "recommended_band",
        "recommended_label",
        "change_from_baseline",
        "do_not_write",
        "next_step",
    }
    if not required.issubset(columns):
        return True

    outcome = columns.get("outcome")
    # PRAGMA table_info: notnull == 0 is required in v3.
    return outcome is None or int(outcome["notnull"]) != 0


def rebuild_refresh_event_results(conn: sqlite3.Connection) -> None:
    old_columns = table_columns(conn, "refresh_event_results")

    conn.execute("DROP TABLE IF EXISTS refresh_event_results_v3")
    conn.execute(REFRESH_EVENT_RESULTS_V3_SQL)

    destination = [
        "refresh_event_result_id",
        "refresh_id",
        "event_id",
        "event_title_snapshot",
        "baseline_assessment_id",
        "baseline_probability",
        "job_state",
        "outcome",
        "cron_id",
        "expected_run_at",
        "cron_run_at_ms",
        "session_id",
        "session_key",
        "source_run_key",
        "cron_summary_preview",
        "transcript_raw",
        "transcript_sha256",
        "parser_version",
        "parse_error",
        "collected_at",
        "cron_status",
        "duration_ms",
        "model",
        "provider",
        "delivered",
        "delivery_status",
        "search_window",
        "no_evidence_reason",
        "recommended_band",
        "recommended_label",
        "change_from_baseline",
        "do_not_write",
        "next_step",
        "new_evidence_count",
        "indicator_count",
        "counterindicator_count",
        "recommended_probability",
        "recommendation_confidence",
        "change_recommended",
        "recommendation_reason",
        "summary",
        "created_at",
    ]

    expressions: list[str] = []
    for name in destination:
        if name == "job_state" and name not in old_columns:
            expressions.append("'completed' AS job_state")
        elif name in old_columns:
            expressions.append(name)
        elif name in {"new_evidence_count", "indicator_count", "counterindicator_count", "change_recommended"}:
            expressions.append(f"0 AS {name}")
        else:
            expressions.append(f"NULL AS {name}")

    # created_at always existed in v2, but guard older/manual schemas.
    if "created_at" not in old_columns:
        idx = destination.index("created_at")
        expressions[idx] = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS created_at"

    conn.execute(
        f"""
        INSERT INTO refresh_event_results_v3 ({", ".join(destination)})
        SELECT {", ".join(expressions)}
        FROM refresh_event_results
        """
    )

    conn.execute("DROP TABLE refresh_event_results")
    conn.execute(
        "ALTER TABLE refresh_event_results_v3 RENAME TO refresh_event_results"
    )


def apply_migration(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "refresh_runs") or not table_exists(conn, "refresh_event_results"):
        raise RuntimeError("v2 refresh audit tables are missing; apply migration 002 first")

    add_refresh_run_columns(conn)

    # Rebuilding a parent of refresh_candidate_evidence is unnecessary if v3
    # has already been applied. On the first v3 run the candidate table does
    # not exist yet.
    if needs_result_rebuild(conn):
        if table_exists(conn, "refresh_candidate_evidence"):
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM refresh_candidate_evidence"
            ).fetchone()["n"]
            if count:
                raise RuntimeError(
                    "refresh_event_results requires rebuild but refresh_candidate_evidence "
                    "already contains rows; refusing ambiguous destructive rebuild"
                )
            conn.execute("DROP TABLE refresh_candidate_evidence")
        rebuild_refresh_event_results(conn)

    conn.execute(CANDIDATE_TABLE_SQL)
    conn.executescript(INDEX_SQL)

    conn.execute(
        """
        INSERT INTO meta(key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (TARGET_VERSION,),
    )


def validate(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []

    for table in ("refresh_runs", "refresh_event_results", "refresh_candidate_evidence"):
        if not table_exists(conn, table):
            errors.append(f"missing table: {table}")

    if errors:
        return errors

    run_columns = table_columns(conn, "refresh_runs")
    for name in REFRESH_RUNS_COLUMNS:
        if name not in run_columns:
            errors.append(f"refresh_runs missing column: {name}")

    result_columns = table_columns(conn, "refresh_event_results")
    for name in (
        "job_state",
        "cron_id",
        "expected_run_at",
        "cron_run_at_ms",
        "session_id",
        "source_run_key",
        "transcript_raw",
        "transcript_sha256",
        "parser_version",
        "do_not_write",
        "next_step",
    ):
        if name not in result_columns:
            errors.append(f"refresh_event_results missing column: {name}")

    outcome = result_columns.get("outcome")
    if outcome is not None and int(outcome["notnull"]) != 0:
        errors.append("refresh_event_results.outcome must be nullable")

    candidate_columns = table_columns(conn, "refresh_candidate_evidence")
    for name in (
        "candidate_id",
        "refresh_event_result_id",
        "ordinal",
        "direction",
        "title",
        "url",
        "why_it_matters",
        "duplicate_risk",
        "freshness",
    ):
        if name not in candidate_columns:
            errors.append(f"refresh_candidate_evidence missing column: {name}")

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PROROK schema migration v2 -> v3")
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate v3 state without changing the database",
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
        conn.execute("PRAGMA busy_timeout = 5000")

        before_version = schema_version(conn)

        if args.check_only:
            errors = validate(conn)
            print("PROROK migration v3 check")
            print(f"db: {db}")
            print(f"schema_version: {before_version}")
            print(f"refresh_runs: {'yes' if table_exists(conn, 'refresh_runs') else 'no'}")
            print(
                "refresh_event_results: "
                + ("yes" if table_exists(conn, "refresh_event_results") else "no")
            )
            print(
                "refresh_candidate_evidence: "
                + ("yes" if table_exists(conn, "refresh_candidate_evidence") else "no")
            )
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("v3_schema: ok")
            return 0

        if before_version not in {"2", "3"}:
            raise RuntimeError(
                f"expected schema_version 2 or 3 before migration, got {before_version!r}"
            )

        conn.execute("BEGIN IMMEDIATE")
        apply_migration(conn)

        errors = validate(conn)
        if errors:
            raise RuntimeError("; ".join(errors))

        after_version = schema_version(conn)
        if after_version != TARGET_VERSION:
            raise RuntimeError(
                f"meta.schema_version expected {TARGET_VERSION}, got {after_version!r}"
            )

        conn.commit()

        print("OK: PROROK schema migration v3 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print(f"schema_version_after: {after_version}")
        print("refresh_runs lifecycle columns: yes")
        print("refresh_event_results collector lifecycle: yes")
        print("refresh_candidate_evidence: yes")
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
