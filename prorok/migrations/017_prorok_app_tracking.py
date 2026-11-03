#!/usr/bin/env python3
"""PROROK schema v17: append-only PROROK_APP evidence tracking."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "17"

DDL = """
CREATE TABLE evidence_prorok_app_status_history (
    prorok_app_status_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('marked','unmarked')),
    source TEXT NOT NULL,
    actor TEXT,
    changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id) ON DELETE RESTRICT
);
CREATE INDEX idx_evidence_prorok_app_status_history_evidence
ON evidence_prorok_app_status_history(evidence_id, changed_at DESC, prorok_app_status_history_id DESC);
"""


def dbpath(value):
    return Path(value or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()


def version(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return None if row is None else str(row[0])


def exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def validate(conn):
    errors = []
    table = "evidence_prorok_app_status_history"
    if not exists(conn, table):
        return [f"missing table: {table}"]
    columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
    for name in ("prorok_app_status_history_id", "evidence_id", "state", "source", "actor", "changed_at"):
        if name not in columns:
            errors.append(f"missing column: {table}.{name}")
    bad = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE state NOT IN ('marked','unmarked')").fetchone()[0]
    if bad:
        errors.append(f"invalid PROROK_APP state rows: {bad}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check failed")
    return errors


def migrate(conn):
    evidence_before = conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0]
    conn.executescript(DDL)
    evidence_after = conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0]
    if evidence_after != evidence_before:
        raise RuntimeError("existing evidence rows changed")
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
        (TARGET_VERSION,),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    path = dbpath(args.db)
    if not path.exists():
        print(f"ERROR: DB not found: {path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        before = version(conn)
        if args.check_only:
            errors = validate(conn)
            if before != TARGET_VERSION:
                errors.append(f"schema_version expected 17, got {before!r}")
            if errors:
                for error in errors:
                    print("ERROR:", error)
                return 1
            print("OK: PROROK schema v17 verified")
            return 0
        if before != "16":
            raise RuntimeError(f"schema_version expected 16 before migration, got {before!r}")
        conn.execute("BEGIN IMMEDIATE")
        migrate(conn)
        errors = validate(conn)
        if errors:
            raise RuntimeError("; ".join(errors))
        conn.commit()
        print("OK: PROROK schema migration v17 applied")
        print("schema_version_after: 17")
        print("existing evidence preserved: yes")
        print("foreign_key_check: ok")
        return 0
    except Exception as exc:
        conn.rollback()
        print("ERROR:", exc, file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
