#!/usr/bin/env python3
"""PROROK schema v14: allow neutral direction for refresh candidate evidence.

SQLite cannot alter a CHECK constraint in place, so this migration rebuilds only
refresh_candidate_evidence while preserving candidate ids and all existing rows.
Official evidence, assessments, events, sources, decisions, and recommendations
are not modified.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "14"

TABLE_SQL = """
CREATE TABLE refresh_candidate_evidence_v14 (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_event_result_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    direction TEXT NOT NULL CHECK(direction IN ('indicator', 'counterindicator', 'neutral')),
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
    validation_state TEXT NOT NULL DEFAULT 'legacy_unvalidated'
        CHECK(validation_state IN (
            'legacy_unvalidated','accepted','rejected_source_policy',
            'rejected_date_conflict','rejected_invalid_metadata'
        )),
    rejection_reason TEXT,
    FOREIGN KEY(refresh_event_result_id)
        REFERENCES refresh_event_results(refresh_event_result_id)
        ON DELETE CASCADE,
    UNIQUE(refresh_event_result_id, ordinal)
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_refresh_candidate_result
ON refresh_candidate_evidence(refresh_event_result_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_refresh_candidate_validation
ON refresh_candidate_evidence(validation_state, refresh_event_result_id, ordinal);
"""

COPY_COLUMNS = (
    "candidate_id,refresh_event_result_id,ordinal,direction,strength,relevance,credibility,"
    "title,source,url,published_at,summary,why_it_matters,duplicate_risk,freshness,created_at,"
    "validation_state,rejection_reason"
)


def resolve_db(explicit: str | None) -> Path:
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()


def version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return None if row is None else str(row[0])


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def validate(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    if not table_exists(conn, "refresh_candidate_evidence"):
        return ["missing table: refresh_candidate_evidence"]

    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='refresh_candidate_evidence'"
    ).fetchone()
    ddl = str(ddl_row[0] or "") if ddl_row else ""
    if "'neutral'" not in ddl:
        errors.append("refresh_candidate_evidence direction CHECK does not allow neutral")

    invalid = conn.execute(
        "SELECT COUNT(*) FROM refresh_candidate_evidence "
        "WHERE direction NOT IN ('indicator','counterindicator','neutral')"
    ).fetchone()[0]
    if invalid:
        errors.append(f"invalid candidate direction rows: {invalid}")

    if conn.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check failed")
    return errors


def apply_migration(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "refresh_candidate_evidence"):
        raise RuntimeError("refresh_candidate_evidence is missing")

    before_count = conn.execute("SELECT COUNT(*) FROM refresh_candidate_evidence").fetchone()[0]
    before_max_id = conn.execute("SELECT MAX(candidate_id) FROM refresh_candidate_evidence").fetchone()[0]

    conn.execute("DROP TABLE IF EXISTS refresh_candidate_evidence_v14")
    conn.execute(TABLE_SQL)
    conn.execute(
        f"INSERT INTO refresh_candidate_evidence_v14({COPY_COLUMNS}) "
        f"SELECT {COPY_COLUMNS} FROM refresh_candidate_evidence"
    )
    conn.execute("DROP TABLE refresh_candidate_evidence")
    conn.execute("ALTER TABLE refresh_candidate_evidence_v14 RENAME TO refresh_candidate_evidence")
    conn.executescript(INDEX_SQL)

    after_count = conn.execute("SELECT COUNT(*) FROM refresh_candidate_evidence").fetchone()[0]
    after_max_id = conn.execute("SELECT MAX(candidate_id) FROM refresh_candidate_evidence").fetchone()[0]
    if (before_count, before_max_id) != (after_count, after_max_id):
        raise RuntimeError(
            f"candidate preservation check failed: before={(before_count, before_max_id)} after={(after_count, after_max_id)}"
        )

    conn.execute(
        """INSERT INTO meta(key,value) VALUES('schema_version',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,
        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
        (TARGET_VERSION,),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PROROK schema migration v13 -> v14 neutral candidate direction")
    parser.add_argument("--db")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    db = resolve_db(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db), timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        before = version(conn)

        if args.check_only:
            errors = validate(conn)
            if before != TARGET_VERSION:
                errors.append(f"schema_version expected 14, got {before!r}")
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("OK: PROROK schema v14 verified")
            return 0

        if before != "13":
            raise RuntimeError(f"schema_version expected 13 before migration, got {before!r}")

        conn.execute("BEGIN IMMEDIATE")
        apply_migration(conn)
        errors = validate(conn)
        if errors:
            raise RuntimeError("; ".join(errors))
        conn.commit()

        print("OK: PROROK schema migration v14 applied")
        print(f"schema_version_before: {before}")
        print("schema_version_after: 14")
        print("candidate neutral direction: yes")
        print("candidate ids/rows preserved: yes")
        print("official evidence/assessments changed: no")
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
