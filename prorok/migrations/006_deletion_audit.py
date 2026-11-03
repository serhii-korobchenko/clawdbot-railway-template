#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v5 to v6 deletion audit.

v6 adds an append-only audit table for explicit hard-delete operations initiated
through deterministic control surfaces such as Telegram. The audit table keeps
snapshots rather than foreign keys on purpose, so its records survive deletion
of the referenced event, evidence, assessment, or source rows.

This migration only adds deletion_audit and indexes. It never deletes events,
evidence, assessments, sources, refresh results, or decisions.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "6"

TARGET_TYPES = ("event", "evidence")

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS deletion_audit (
    deletion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL
        CHECK(target_type IN ('event', 'evidence')),
    target_id_snapshot TEXT NOT NULL,
    event_id_snapshot TEXT,
    target_label_snapshot TEXT,
    source_id_snapshot INTEGER,
    assessment_count INTEGER NOT NULL DEFAULT 0
        CHECK(assessment_count >= 0),
    evidence_count INTEGER NOT NULL DEFAULT 0
        CHECK(evidence_count >= 0),
    decision_source TEXT NOT NULL DEFAULT 'telegram',
    actor_snapshot TEXT,
    deleted_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_deletion_audit_event
ON deletion_audit(event_id_snapshot, deleted_at DESC, deletion_id DESC);

CREATE INDEX IF NOT EXISTS idx_deletion_audit_target
ON deletion_audit(target_type, target_id_snapshot, deleted_at DESC, deletion_id DESC);
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


def apply_migration(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "refresh_user_decisions"):
        raise RuntimeError(
            "refresh_user_decisions is missing; apply migration 005 before migration 006"
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

    if not table_exists(conn, "deletion_audit"):
        return ["missing table: deletion_audit"]

    required_columns = {
        "deletion_id",
        "target_type",
        "target_id_snapshot",
        "event_id_snapshot",
        "target_label_snapshot",
        "source_id_snapshot",
        "assessment_count",
        "evidence_count",
        "decision_source",
        "actor_snapshot",
        "deleted_at",
    }
    columns = table_columns(conn, "deletion_audit")
    for name in sorted(required_columns):
        if name not in columns:
            errors.append(f"deletion_audit missing column: {name}")

    invalid_target_types = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM deletion_audit
        WHERE target_type NOT IN ('event', 'evidence')
        """
    ).fetchone()["n"]
    if invalid_target_types:
        errors.append(f"invalid target_type rows: {invalid_target_types}")

    invalid_counts = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM deletion_audit
        WHERE assessment_count < 0 OR evidence_count < 0
        """
    ).fetchone()["n"]
    if invalid_counts:
        errors.append(f"negative deletion count rows: {invalid_counts}")

    # Intentionally no foreign keys: snapshots must survive hard deletes.
    audit_fks = conn.execute("PRAGMA foreign_key_list(deletion_audit)").fetchall()
    if audit_fks:
        errors.append(
            f"deletion_audit unexpectedly has {len(audit_fks)} foreign key(s)"
        )

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply PROROK schema migration v5 -> v6 deletion audit"
    )
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate v6 state without changing the database",
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
            print("PROROK migration v6 check")
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
            print("v6_schema: ok")
            return 0

        if before_version not in {"5", "6"}:
            raise RuntimeError(
                f"expected schema_version 5 or 6 before migration, got {before_version!r}"
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

        print("OK: PROROK schema migration v6 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print(f"schema_version_after: {after_version}")
        print("deletion_audit table: yes")
        print("snapshot-only audit foreign keys: none")
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
