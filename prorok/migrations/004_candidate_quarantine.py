#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v3 to v4 candidate quarantine.

v4 adds candidate-level validation state and event-level recommendation validity.
It preserves all historical refresh rows and never writes official assessments,
evidence_items, sources, events, or historical manual runs.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "4"

CANDIDATE_COLUMNS = {
    "validation_state": (
        "TEXT NOT NULL DEFAULT 'legacy_unvalidated' "
        "CHECK(validation_state IN ("
        "'legacy_unvalidated','accepted','rejected_source_policy',"
        "'rejected_date_conflict','rejected_invalid_metadata'))"
    ),
    "rejection_reason": "TEXT",
}

RESULT_COLUMNS = {
    "candidate_rejected_count": (
        "INTEGER NOT NULL DEFAULT 0 CHECK(candidate_rejected_count >= 0)"
    ),
    "recommendation_valid": (
        "INTEGER NOT NULL DEFAULT 1 CHECK(recommendation_valid IN (0, 1))"
    ),
}

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_refresh_candidate_validation
ON refresh_candidate_evidence(validation_state, refresh_event_result_id, ordinal);
"""


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).resolve()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone() is not None


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


def add_columns(
    conn: sqlite3.Connection,
    table: str,
    definitions: dict[str, str],
) -> None:
    columns = table_columns(conn, table)
    for name, definition in definitions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def apply_migration(conn: sqlite3.Connection) -> None:
    for table in ("refresh_event_results", "refresh_candidate_evidence"):
        if not table_exists(conn, table):
            raise RuntimeError(
                f"{table} is missing; apply migration 003 before migration 004"
            )

    add_columns(conn, "refresh_candidate_evidence", CANDIDATE_COLUMNS)
    add_columns(conn, "refresh_event_results", RESULT_COLUMNS)
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

    for table in ("refresh_event_results", "refresh_candidate_evidence"):
        if not table_exists(conn, table):
            errors.append(f"missing table: {table}")

    if errors:
        return errors

    candidate_columns = table_columns(conn, "refresh_candidate_evidence")
    for name in CANDIDATE_COLUMNS:
        if name not in candidate_columns:
            errors.append(f"refresh_candidate_evidence missing column: {name}")

    result_columns = table_columns(conn, "refresh_event_results")
    for name in RESULT_COLUMNS:
        if name not in result_columns:
            errors.append(f"refresh_event_results missing column: {name}")

    invalid_states = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refresh_candidate_evidence
        WHERE validation_state NOT IN (
            'legacy_unvalidated',
            'accepted',
            'rejected_source_policy',
            'rejected_date_conflict',
            'rejected_invalid_metadata'
        )
        """
    ).fetchone()["n"]
    if invalid_states:
        errors.append(f"invalid candidate validation_state rows: {invalid_states}")

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PROROK schema migration v3 -> v4")
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate v4 state without changing the database",
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
            print("PROROK migration v4 check")
            print(f"db: {db}")
            print(f"schema_version: {before_version}")
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("v4_schema: ok")
            return 0

        if before_version not in {"3", "4"}:
            raise RuntimeError(
                f"expected schema_version 3 or 4 before migration, got {before_version!r}"
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

        print("OK: PROROK schema migration v4 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print(f"schema_version_after: {after_version}")
        print("candidate quarantine columns: yes")
        print("recommendation validity columns: yes")
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
