#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v6 to v7 search-quality audit.

v7 adds deterministic audit fields for the refresh search phase. Historical rows
remain untouched (NULL metrics). The migration does not alter official events,
assessments, evidence_items, sources, refresh candidates, or user decisions.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "7"

RESULT_COLUMNS = {
    "search_call_count": (
        "INTEGER CHECK(search_call_count IS NULL OR search_call_count >= 0)"
    ),
    "distinct_search_query_count": (
        "INTEGER CHECK(distinct_search_query_count IS NULL OR distinct_search_query_count >= 0)"
    ),
    "search_quality_valid": (
        "INTEGER CHECK(search_quality_valid IS NULL OR search_quality_valid IN (0, 1))"
    ),
    "search_quality_reason": "TEXT",
}

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_refresh_event_results_search_quality
ON refresh_event_results(search_quality_valid, refresh_id, refresh_event_result_id);
"""


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(
        os.getenv("PROROK_DB_PATH")
        or os.getenv("PROROK_DB")
        or DEFAULT_DB
    ).resolve()


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
    if not table_exists(conn, "refresh_event_results"):
        raise RuntimeError(
            "refresh_event_results is missing; apply migration 003 before migration 007"
        )
    if not table_exists(conn, "deletion_audit"):
        raise RuntimeError(
            "deletion_audit is missing; apply migration 006 before migration 007"
        )

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
    if not table_exists(conn, "refresh_event_results"):
        return ["missing table: refresh_event_results"]

    columns = table_columns(conn, "refresh_event_results")
    for name in sorted(RESULT_COLUMNS):
        if name not in columns:
            errors.append(f"refresh_event_results missing column: {name}")

    if not errors:
        invalid_counts = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM refresh_event_results
            WHERE (search_call_count IS NOT NULL AND search_call_count < 0)
               OR (distinct_search_query_count IS NOT NULL AND distinct_search_query_count < 0)
            """
        ).fetchone()["n"]
        if invalid_counts:
            errors.append(f"invalid search count rows: {invalid_counts}")

        invalid_validity = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM refresh_event_results
            WHERE search_quality_valid IS NOT NULL
              AND search_quality_valid NOT IN (0, 1)
            """
        ).fetchone()["n"]
        if invalid_validity:
            errors.append(f"invalid search_quality_valid rows: {invalid_validity}")

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply PROROK schema migration v6 -> v7 search-quality audit"
    )
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate v7 state without changing the database",
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
            print("PROROK migration v7 check")
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
            print("v7_schema: ok")
            return 0

        if before_version not in {"6", "7"}:
            raise RuntimeError(
                f"expected schema_version 6 or 7 before migration, got {before_version!r}"
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

        print("OK: PROROK schema migration v7 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print(f"schema_version_after: {after_version}")
        print("search-quality audit columns: yes")
        print("historical refresh rows modified: no")
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
