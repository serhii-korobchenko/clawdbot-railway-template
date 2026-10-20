#!/usr/bin/env python3
"""PROROK schema v10: persistent refresh completion notification audit."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "10"

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS refresh_notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_id INTEGER NOT NULL,
    notification_type TEXT NOT NULL
        CHECK(notification_type IN ('telegram_completion')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'sending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_attempt_at TEXT,
    sent_at TEXT,
    last_error TEXT,
    UNIQUE(refresh_id, notification_type),
    FOREIGN KEY(refresh_id) REFERENCES refresh_runs(refresh_id) ON DELETE CASCADE
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_refresh_notifications_status
ON refresh_notifications(status, notification_type, refresh_id);
"""

def resolve_db(explicit: str | None) -> Path:
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return None if row is None else str(row["value"])

def apply_migration(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "refresh_runs"):
        raise RuntimeError("refresh_runs is missing; apply migrations through v9 first")
    conn.execute(TABLE_SQL)
    conn.executescript(INDEX_SQL)
    conn.execute(
        """INSERT INTO meta(key,value) VALUES('schema_version',?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,
           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
        (TARGET_VERSION,),
    )

def validate(conn: sqlite3.Connection) -> list[str]:
    if not table_exists(conn, "refresh_notifications"):
        return ["missing table: refresh_notifications"]
    required={"notification_id","refresh_id","notification_type","status","attempts","created_at","last_attempt_at","sent_at","last_error"}
    columns={r["name"] for r in conn.execute("PRAGMA table_info(refresh_notifications)")}
    errors=[f"refresh_notifications missing column: {n}" for n in sorted(required-columns)]
    fk=conn.execute("PRAGMA foreign_key_list(refresh_notifications)").fetchall()
    if not any(r["table"]=="refresh_runs" and r["from"]=="refresh_id" for r in fk):
        errors.append("refresh_notifications missing refresh_runs foreign key")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check failed")
    return errors

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--db")
    p.add_argument("--check-only", action="store_true")
    args=p.parse_args()
    db=resolve_db(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr); return 1
    conn=sqlite3.connect(str(db), timeout=30); conn.row_factory=sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA busy_timeout=5000")
        before=schema_version(conn)
        if args.check_only:
            errors=validate(conn)
            if before != TARGET_VERSION: errors.append(f"schema_version expected 10, got {before!r}")
            if errors:
                for e in errors: print(f"ERROR: {e}")
                return 1
            print("PROROK migration v10 check: ok"); return 0
        if before not in {"9","10"}:
            raise RuntimeError(f"expected schema_version 9 or 10 before migration, got {before!r}")
        conn.execute("BEGIN IMMEDIATE"); apply_migration(conn)
        errors=validate(conn)
        if errors: raise RuntimeError("; ".join(errors))
        conn.commit()
        print("OK: PROROK schema migration v10 applied")
        print(f"schema_version_before: {before}")
        print("schema_version_after: 10")
        print("refresh_notifications table: yes")
        print("refresh data changed: no")
        print("foreign_key_check: ok")
        return 0
    except Exception as exc:
        conn.rollback(); print(f"ERROR: {exc}", file=sys.stderr); return 1
    finally:
        conn.close()

if __name__=="__main__":
    raise SystemExit(main())
