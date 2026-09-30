#!/usr/bin/env python3
"""PROROK schema v11: structured forecast calibration audit fields.

Adds structured calibration metadata to refresh_event_results. Existing refresh
rows remain valid and receive NULL calibration fields. No official assessment,
evidence, event, or probability data is changed.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "11"

COLUMNS = {
    "probability_delta": "INTEGER",
    "net_evidence_direction": "TEXT CHECK(net_evidence_direction IN ('positive','negative','balanced') OR net_evidence_direction IS NULL)",
    "net_evidence_impact": "TEXT CHECK(net_evidence_impact IN ('none','weak','moderate','strong') OR net_evidence_impact IS NULL)",
    "baseline_incorporation": "TEXT CHECK(baseline_incorporation IN ('low','medium','high') OR baseline_incorporation IS NULL)",
    "category_transition": "INTEGER CHECK(category_transition IN (0,1) OR category_transition IS NULL)",
    "delta_justification": "TEXT",
}

def resolve_db(explicit: str | None) -> Path:
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return None if row is None else str(row["value"])

def apply_migration(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "refresh_event_results"):
        raise RuntimeError("refresh_event_results is missing; apply migrations through v10 first")
    existing = table_columns(conn, "refresh_event_results")
    for name, definition in COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE refresh_event_results ADD COLUMN {name} {definition}")
    conn.execute(
        """INSERT INTO meta(key,value) VALUES('schema_version',?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,
           updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
        (TARGET_VERSION,),
    )

def validate(conn: sqlite3.Connection) -> list[str]:
    if not table_exists(conn, "refresh_event_results"):
        return ["missing table: refresh_event_results"]
    columns = table_columns(conn, "refresh_event_results")
    errors = [f"refresh_event_results missing column: {name}" for name in sorted(set(COLUMNS) - columns)]
    invalid = conn.execute(
        """SELECT COUNT(*) AS n FROM refresh_event_results
           WHERE (net_evidence_direction IS NOT NULL AND net_evidence_direction NOT IN ('positive','negative','balanced'))
              OR (net_evidence_impact IS NOT NULL AND net_evidence_impact NOT IN ('none','weak','moderate','strong'))
              OR (baseline_incorporation IS NOT NULL AND baseline_incorporation NOT IN ('low','medium','high'))
              OR (category_transition IS NOT NULL AND category_transition NOT IN (0,1))"""
    ).fetchone()["n"]
    if invalid:
        errors.append(f"invalid calibration rows: {invalid}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check failed")
    return errors

def main() -> int:
    p = argparse.ArgumentParser(description="Apply PROROK schema migration v10 -> v11 calibration audit")
    p.add_argument("--db")
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()
    db = resolve_db(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        before = schema_version(conn)
        if args.check_only:
            errors = validate(conn)
            if before != TARGET_VERSION:
                errors.append(f"schema_version expected 11, got {before!r}")
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("PROROK migration v11 check: ok")
            return 0
        if before not in {"10", "11"}:
            raise RuntimeError(f"expected schema_version 10 or 11 before migration, got {before!r}")
        conn.execute("BEGIN IMMEDIATE")
        apply_migration(conn)
        errors = validate(conn)
        if errors:
            raise RuntimeError("; ".join(errors))
        if schema_version(conn) != TARGET_VERSION:
            raise RuntimeError("schema_version was not updated to 11")
        conn.commit()
        print("OK: PROROK schema migration v11 applied")
        print(f"schema_version_before: {before}")
        print("schema_version_after: 11")
        print("calibration columns: yes")
        print("refresh/official data changed: no")
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
