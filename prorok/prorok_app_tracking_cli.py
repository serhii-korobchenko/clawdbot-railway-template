#!/usr/bin/env python3
"""Read and change append-only PROROK_APP tracking state for official evidence."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
REQUIRED_SCHEMA_VERSION = 17


def dbpath(value: str | None) -> Path:
    return Path(value or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def require_schema(conn: sqlite3.Connection) -> None:
    version = schema_version(conn)
    if version < REQUIRED_SCHEMA_VERSION:
        raise RuntimeError(f"schema_version >= {REQUIRED_SCHEMA_VERSION} required, got {version}")


def require_evidence(conn: sqlite3.Connection, evidence_id: int) -> None:
    if conn.execute("SELECT 1 FROM evidence_items WHERE evidence_id=?", (evidence_id,)).fetchone() is None:
        raise RuntimeError(f"evidence not found: {evidence_id}")


def current_row(conn: sqlite3.Connection, evidence_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT prorok_app_status_history_id, evidence_id, state, source, actor, changed_at
        FROM evidence_prorok_app_status_history
        WHERE evidence_id=?
        ORDER BY changed_at DESC, prorok_app_status_history_id DESC
        LIMIT 1
        """,
        (evidence_id,),
    ).fetchone()


def print_state(row: sqlite3.Row | None, evidence_id: int, *, changed: bool) -> None:
    print(f"evidence_id: {evidence_id}")
    print(f"state: {row['state'] if row else 'unmarked'}")
    print(f"changed: {'true' if changed else 'false'}")
    print(f"prorok_app_status_history_id: {row['prorok_app_status_history_id'] if row else ''}")
    print(f"source: {row['source'] if row else ''}")
    print(f"actor: {row['actor'] if row and row['actor'] is not None else ''}")
    print(f"changed_at: {row['changed_at'] if row else ''}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show")
    show.add_argument("evidence_id", type=int)

    set_state = sub.add_parser("set")
    set_state.add_argument("evidence_id", type=int)
    set_state.add_argument("--state", choices=("marked", "unmarked"), required=True)
    set_state.add_argument("--source", required=True)
    set_state.add_argument("--actor")

    args = parser.parse_args()
    path = dbpath(args.db)
    if not path.exists():
        print(f"ERROR: DB not found: {path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        require_schema(conn)
        require_evidence(conn, args.evidence_id)

        if args.command == "show":
            print_state(current_row(conn, args.evidence_id), args.evidence_id, changed=False)
            return 0

        conn.execute("BEGIN IMMEDIATE")
        before = current_row(conn, args.evidence_id)
        current_state = before["state"] if before else "unmarked"
        if current_state == args.state:
            conn.rollback()
            print_state(before, args.evidence_id, changed=False)
            return 0

        cursor = conn.execute(
            """
            INSERT INTO evidence_prorok_app_status_history(evidence_id,state,source,actor)
            VALUES(?,?,?,?)
            """,
            (args.evidence_id, args.state, args.source, args.actor),
        )
        row = conn.execute(
            """
            SELECT prorok_app_status_history_id, evidence_id, state, source, actor, changed_at
            FROM evidence_prorok_app_status_history
            WHERE prorok_app_status_history_id=?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        conn.commit()
        print_state(row, args.evidence_id, changed=True)
        return 0
    except Exception as exc:
        if conn.in_transaction:
            conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
