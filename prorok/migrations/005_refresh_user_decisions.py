#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v4 to v5 refresh user decisions.

v5 adds an explicit audit trail for a user's final decision on a completed
refresh recommendation. The migration only adds the decision table and indexes;
it never creates assessments or changes official probabilities.

One final decision is allowed per refresh_event_result_id. A decision may later
point to the assessment created by deterministic apply-decision logic, while
keep_current intentionally leaves assessment_id NULL.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "5"

DECISION_TYPES = (
    "accept_recommendation",
    "custom_probability",
    "keep_current",
)

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS refresh_user_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_event_result_id INTEGER NOT NULL UNIQUE,
    event_id_snapshot TEXT NOT NULL,
    decision_type TEXT NOT NULL
        CHECK(decision_type IN (
            'accept_recommendation',
            'custom_probability',
            'keep_current'
        )),
    baseline_assessment_id INTEGER,
    baseline_probability INTEGER NOT NULL
        CHECK(baseline_probability BETWEEN 0 AND 100),
    recommended_probability INTEGER
        CHECK(recommended_probability IS NULL OR recommended_probability BETWEEN 0 AND 100),
    selected_probability INTEGER NOT NULL
        CHECK(selected_probability BETWEEN 0 AND 100),
    assessment_id INTEGER UNIQUE,
    decision_source TEXT NOT NULL DEFAULT 'telegram',
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    FOREIGN KEY(refresh_event_result_id)
        REFERENCES refresh_event_results(refresh_event_result_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(baseline_assessment_id)
        REFERENCES assessments(assessment_id)
        ON DELETE SET NULL,
    FOREIGN KEY(assessment_id)
        REFERENCES assessments(assessment_id)
        ON DELETE SET NULL
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_refresh_user_decisions_event
ON refresh_user_decisions(event_id_snapshot, decided_at DESC, decision_id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_user_decisions_assessment
ON refresh_user_decisions(assessment_id)
WHERE assessment_id IS NOT NULL;
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


def apply_migration(conn: sqlite3.Connection) -> None:
    for table in ("refresh_event_results", "assessments"):
        if not table_exists(conn, table):
            raise RuntimeError(
                f"{table} is missing; apply migrations through v4 before migration 005"
            )

    conn.execute(TABLE_SQL)
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

    if not table_exists(conn, "refresh_user_decisions"):
        return ["missing table: refresh_user_decisions"]

    required_columns = {
        "decision_id",
        "refresh_event_result_id",
        "event_id_snapshot",
        "decision_type",
        "baseline_assessment_id",
        "baseline_probability",
        "recommended_probability",
        "selected_probability",
        "assessment_id",
        "decision_source",
        "decided_at",
    }
    columns = table_columns(conn, "refresh_user_decisions")
    for name in sorted(required_columns):
        if name not in columns:
            errors.append(f"refresh_user_decisions missing column: {name}")

    invalid_decisions = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refresh_user_decisions
        WHERE decision_type NOT IN (
            'accept_recommendation',
            'custom_probability',
            'keep_current'
        )
        """
    ).fetchone()["n"]
    if invalid_decisions:
        errors.append(f"invalid decision_type rows: {invalid_decisions}")

    duplicate_refresh_results = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM (
            SELECT refresh_event_result_id
            FROM refresh_user_decisions
            GROUP BY refresh_event_result_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()["n"]
    if duplicate_refresh_results:
        errors.append(
            f"duplicate refresh_event_result_id decision groups: {duplicate_refresh_results}"
        )

    invalid_keep_current = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refresh_user_decisions
        WHERE decision_type = 'keep_current'
          AND assessment_id IS NOT NULL
        """
    ).fetchone()["n"]
    if invalid_keep_current:
        errors.append(
            f"keep_current rows unexpectedly linked to assessment: {invalid_keep_current}"
        )

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply PROROK schema migration v4 -> v5 refresh user decisions"
    )
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate v5 state without changing the database",
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
            print("PROROK migration v5 check")
            print(f"db: {db}")
            print(f"schema_version: {before_version}")
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            if before_version != TARGET_VERSION:
                print(
                    f"ERROR: schema_version expected {TARGET_VERSION}, got {before_version!r}"
                )
                return 1
            print("v5_schema: ok")
            return 0

        if before_version not in {"4", "5"}:
            raise RuntimeError(
                f"expected schema_version 4 or 5 before migration, got {before_version!r}"
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

        print("OK: PROROK schema migration v5 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print(f"schema_version_after: {after_version}")
        print("refresh_user_decisions table: yes")
        print("one final decision per refresh_event_result_id: yes")
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
