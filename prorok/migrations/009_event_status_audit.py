#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v8 to v9 event-status audit.

v9 adds an append-only audit trail for deterministic event lifecycle changes
initiated from Telegram or manual CLI. Audit rows intentionally keep snapshots
without foreign keys so they survive a later hard-delete of the event.

This migration never changes any event status, probability, assessment, or
official evidence.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "9"

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS event_status_audit (
    status_change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id_snapshot TEXT NOT NULL,
    title_snapshot TEXT,
    from_status TEXT NOT NULL
        CHECK(from_status IN ('active', 'paused', 'resolved', 'archived')),
    to_status TEXT NOT NULL
        CHECK(to_status IN ('active', 'paused', 'resolved', 'archived')),
    decision_source TEXT NOT NULL DEFAULT 'telegram',
    actor_snapshot TEXT,
    changed_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_event_status_audit_event
ON event_status_audit(event_id_snapshot, changed_at DESC, status_change_id DESC);
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


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return None if row is None else str(row["value"])


def apply_migration(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "events"):
        raise RuntimeError("events is missing; apply migrations through v8 before migration 009")

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
    if not table_exists(conn, "event_status_audit"):
        return ["missing table: event_status_audit"]

    required = {
        "status_change_id",
        "event_id_snapshot",
        "title_snapshot",
        "from_status",
        "to_status",
        "decision_source",
        "actor_snapshot",
        "changed_at",
    }
    columns = table_columns(conn, "event_status_audit")
    for name in sorted(required - columns):
        errors.append(f"event_status_audit missing column: {name}")

    invalid = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM event_status_audit
        WHERE from_status NOT IN ('active', 'paused', 'resolved', 'archived')
           OR to_status NOT IN ('active', 'paused', 'resolved', 'archived')
        """
    ).fetchone()["n"]
    if invalid:
        errors.append(f"invalid status audit rows: {invalid}")

    if conn.execute("PRAGMA foreign_key_list(event_status_audit)").fetchall():
        errors.append("event_status_audit must not have foreign keys")

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply PROROK schema migration v8 -> v9 event-status audit"
    )
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument("--check-only", action="store_true")
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
        before = schema_version(conn)

        if args.check_only:
            errors = validate(conn)
            print("PROROK migration v9 check")
            print(f"db: {db}")
            print(f"schema_version: {before}")
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            if before != TARGET_VERSION:
                print(
                    f"ERROR: schema_version expected {TARGET_VERSION}, got {before!r}",
                    file=sys.stderr,
                )
                return 1
            print("v9_schema: ok")
            return 0

        if before not in {"8", "9"}:
            raise RuntimeError(
                f"expected schema_version 8 or 9 before migration, got {before!r}"
            )

        conn.execute("BEGIN IMMEDIATE")
        apply_migration(conn)
        errors = validate(conn)
        if errors:
            raise RuntimeError("; ".join(errors))
        if schema_version(conn) != TARGET_VERSION:
            raise RuntimeError("schema_version was not updated to 9")
        conn.commit()

        print("OK: PROROK schema migration v9 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before}")
        print("schema_version_after: 9")
        print("event_status_audit table: yes")
        print("snapshot-only audit foreign keys: none")
        print("event statuses changed: no")
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
