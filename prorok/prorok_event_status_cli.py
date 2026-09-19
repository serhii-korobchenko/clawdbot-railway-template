#!/usr/bin/env python3
"""Deterministic PROROK event lifecycle status CLI.

This CLI is the write boundary for explicit status changes initiated from
Telegram or manual CLI. It never asks an LLM to choose or infer a status.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
MIN_SCHEMA_VERSION = 9
ALL_STATUSES = ("active", "paused", "resolved", "archived")
TARGET_STATUSES = ("active", "paused", "archived")
SOURCE_CHOICES = ("telegram", "manual_cli", "system")
ALLOWED_TRANSITIONS = {
    ("active", "paused"),
    ("paused", "active"),
    ("active", "archived"),
    ("paused", "archived"),
    ("archived", "active"),
}


class CliError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(
        os.getenv("PROROK_DB_PATH")
        or os.getenv("PROROK_DB")
        or DEFAULT_DB
    ).resolve()


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise CliError(f"PROROK DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[object] = (),
) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def require_schema_v9(conn: sqlite3.Connection) -> None:
    row = fetch_one(conn, "SELECT value FROM meta WHERE key = 'schema_version'")
    version = None if row is None else str(row["value"])
    try:
        number = int(version) if version is not None else None
    except (TypeError, ValueError) as exc:
        raise CliError(f"schema v9+ required; current schema_version={version!r}") from exc
    if number is None or number < MIN_SCHEMA_VERSION:
        raise CliError(f"schema v9+ required; current schema_version={version!r}")

    if fetch_one(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_status_audit'",
    ) is None:
        raise CliError("schema v9 table event_status_audit is missing")


def latest_matching_audit(
    conn: sqlite3.Connection,
    event_id: str,
    from_status: str,
    to_status: str,
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        """
        SELECT status_change_id, changed_at
        FROM event_status_audit
        WHERE event_id_snapshot = ?
          AND from_status = ?
          AND to_status = ?
        ORDER BY status_change_id DESC
        LIMIT 1
        """,
        (event_id, from_status, to_status),
    )


def validate_integrity(conn: sqlite3.Connection) -> None:
    errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if errors:
        raise CliError(f"foreign_key_check failed with {len(errors)} error(s)")


def cmd_set_status(args: argparse.Namespace) -> int:
    db = resolve_db(args.db)
    event_id = str(args.event_id)
    expected = str(args.from_status)
    target = str(args.to_status)

    if (expected, target) not in ALLOWED_TRANSITIONS:
        raise CliError(f"unsupported status transition: {expected} -> {target}")

    with connect(db) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            require_schema_v9(conn)
            event = fetch_one(
                conn,
                """
                SELECT event_id, title, status, archived_at
                FROM events
                WHERE event_id = ?
                """,
                (event_id,),
            )
            if event is None:
                raise CliError(f"event_id not found: {event_id}")

            current = str(event["status"])
            if current == target:
                audit = latest_matching_audit(conn, event_id, expected, target)
                conn.rollback()
                print("OK: event already in target status")
                print(f"event_id: {event_id}")
                print(f"from_status: {expected}")
                print(f"to_status: {target}")
                print(f"status_change_id: {audit['status_change_id'] if audit else 'none'}")
                print(f"changed_at: {audit['changed_at'] if audit else 'unknown'}")
                print("idempotent_replay: true")
                return 0

            if current != expected:
                raise CliError(
                    f"stale status transition: expected {expected}, current {current}, target {target}"
                )

            now = utc_now()
            new_archived_at = now if target == "archived" else (
                None if current == "archived" else event["archived_at"]
            )

            cur = conn.execute(
                """
                INSERT INTO event_status_audit(
                    event_id_snapshot,
                    title_snapshot,
                    from_status,
                    to_status,
                    decision_source,
                    actor_snapshot,
                    changed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(event["title"] or event_id),
                    current,
                    target,
                    args.source,
                    args.actor,
                    now,
                ),
            )
            status_change_id = int(cur.lastrowid)

            updated = conn.execute(
                """
                UPDATE events
                SET status = ?,
                    archived_at = ?,
                    updated_at = ?
                WHERE event_id = ?
                  AND status = ?
                """,
                (target, new_archived_at, now, event_id, expected),
            )
            if updated.rowcount != 1:
                raise CliError("status changed concurrently; rolling back")

            validate_integrity(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    print("OK: PROROK event status changed")
    print(f"status_change_id: {status_change_id}")
    print(f"event_id: {event_id}")
    print(f"from_status: {expected}")
    print(f"to_status: {target}")
    print(f"archived_at: {new_archived_at or 'none'}")
    print(f"changed_at: {now}")
    print(f"decision_source: {args.source}")
    print(f"actor_snapshot: {args.actor or 'none'}")
    print("idempotent_replay: false")
    print("atomic_commit: true")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically change a PROROK event lifecycle status"
    )
    parser.add_argument("--db", help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("set-status")
    command.add_argument("event_id")
    command.add_argument("--from-status", required=True, choices=ALL_STATUSES)
    command.add_argument("--to-status", required=True, choices=TARGET_STATUSES)
    command.add_argument("--source", default="telegram", choices=SOURCE_CHOICES)
    command.add_argument("--actor")
    command.set_defaults(func=cmd_set_status)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"SQLITE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
