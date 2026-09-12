#!/usr/bin/env python3
"""Deterministic hard-delete CLI for PROROK event/evidence records.

This CLI is the write boundary for explicit delete actions initiated from
Telegram or manual CLI. It never asks an LLM to decide what should be deleted.

Rules:
- schema v6 is required;
- deletion_audit snapshot is written in the same transaction before deletion;
- deleting evidence never deletes its source;
- deleting an event relies on declared SQLite FK semantics for dependent rows;
- foreign-key integrity is checked before commit;
- repeat deletion of an already-deleted target is idempotent when an audit row
  for that target already exists.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"
REQUIRED_SCHEMA_VERSION = "6"
SOURCE_CHOICES = ("telegram", "manual_cli", "system")


class CliError(RuntimeError):
    """User-facing deterministic delete error."""


def resolve_home(explicit_home: str | None) -> Path:
    if explicit_home:
        return Path(explicit_home).expanduser().resolve()
    if os.getenv("PROROK_HOME"):
        return Path(os.environ["PROROK_HOME"]).expanduser().resolve()
    if os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        return Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]).expanduser().resolve() / "prorok"
    if os.getenv("DATA_DIR"):
        return Path(os.environ["DATA_DIR"]).expanduser().resolve() / "workspace" / "prorok"
    return Path(DEFAULT_PROROK_HOME).resolve()


def resolve_db(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db).expanduser().resolve()
    if os.getenv("PROROK_DB"):
        return Path(os.environ["PROROK_DB"]).expanduser().resolve()
    if os.getenv("PROROK_DB_PATH"):
        return Path(os.environ["PROROK_DB_PATH"]).expanduser().resolve()
    return resolve_home(args.home) / DEFAULT_DB_NAME


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


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = fetch_one(conn, "SELECT value FROM meta WHERE key = 'schema_version'")
    return None if row is None else str(row["value"])


def require_schema_v6(conn: sqlite3.Connection) -> None:
    version = schema_version(conn)
    if version != REQUIRED_SCHEMA_VERSION:
        raise CliError(
            f"schema v{REQUIRED_SCHEMA_VERSION} required; current schema_version={version!r}"
        )
    table = fetch_one(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='deletion_audit'",
    )
    if table is None:
        raise CliError("schema v6 table deletion_audit is missing")


def existing_deletion(
    conn: sqlite3.Connection,
    target_type: str,
    target_id_snapshot: str,
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        """
        SELECT
            deletion_id,
            target_type,
            target_id_snapshot,
            event_id_snapshot,
            target_label_snapshot,
            source_id_snapshot,
            assessment_count,
            evidence_count,
            decision_source,
            actor_snapshot,
            deleted_at
        FROM deletion_audit
        WHERE target_type = ?
          AND target_id_snapshot = ?
        ORDER BY deletion_id DESC
        LIMIT 1
        """,
        (target_type, target_id_snapshot),
    )


def print_existing_deletion(row: sqlite3.Row) -> None:
    print("OK: target already deleted")
    print(f"deletion_id: {row['deletion_id']}")
    print(f"target_type: {row['target_type']}")
    print(f"target_id_snapshot: {row['target_id_snapshot']}")
    print(f"event_id_snapshot: {row['event_id_snapshot'] or 'none'}")
    print(f"decision_source: {row['decision_source']}")
    print(f"deleted_at: {row['deleted_at']}")
    print("idempotent_replay: true")


def insert_audit(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_id_snapshot: str,
    event_id_snapshot: str | None,
    target_label_snapshot: str | None,
    source_id_snapshot: int | None,
    assessment_count: int,
    evidence_count: int,
    decision_source: str,
    actor_snapshot: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO deletion_audit(
            target_type,
            target_id_snapshot,
            event_id_snapshot,
            target_label_snapshot,
            source_id_snapshot,
            assessment_count,
            evidence_count,
            decision_source,
            actor_snapshot
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_type,
            target_id_snapshot,
            event_id_snapshot,
            target_label_snapshot,
            source_id_snapshot,
            assessment_count,
            evidence_count,
            decision_source,
            actor_snapshot,
        ),
    )
    return int(cur.lastrowid)


def validate_integrity(conn: sqlite3.Connection) -> None:
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise CliError(
            f"foreign_key_check failed with {len(fk_errors)} error(s)"
        )


def cmd_delete_evidence(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    evidence_id = int(args.evidence_id)
    target_id = str(evidence_id)

    with connect(db_path) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            require_schema_v6(conn)

            row = fetch_one(
                conn,
                """
                SELECT
                    ei.evidence_id,
                    ei.event_id,
                    ei.source_id,
                    ei.summary,
                    ei.direction,
                    ei.strength,
                    e.title AS event_title
                FROM evidence_items ei
                LEFT JOIN events e ON e.event_id = ei.event_id
                WHERE ei.evidence_id = ?
                """,
                (evidence_id,),
            )

            if row is None:
                existing = existing_deletion(conn, "evidence", target_id)
                if existing is not None:
                    conn.rollback()
                    print_existing_deletion(existing)
                    return 0
                raise CliError(f"evidence_id not found: {evidence_id}")

            label = str(row["summary"] or "").strip() or f"evidence #{evidence_id}"
            deletion_id = insert_audit(
                conn,
                target_type="evidence",
                target_id_snapshot=target_id,
                event_id_snapshot=str(row["event_id"]),
                target_label_snapshot=label,
                source_id_snapshot=int(row["source_id"]),
                assessment_count=0,
                evidence_count=1,
                decision_source=args.source,
                actor_snapshot=args.actor,
            )

            cur = conn.execute(
                "DELETE FROM evidence_items WHERE evidence_id = ?",
                (evidence_id,),
            )
            if cur.rowcount != 1:
                raise CliError(
                    f"expected to delete 1 evidence row, deleted {cur.rowcount}"
                )

            source_still_exists = fetch_one(
                conn,
                "SELECT 1 FROM sources WHERE source_id = ?",
                (int(row["source_id"]),),
            ) is not None
            if not source_still_exists:
                raise CliError(
                    "source row disappeared during evidence delete; rolling back"
                )

            validate_integrity(conn)
            conn.commit()

        except Exception:
            conn.rollback()
            raise

    print("OK: PROROK evidence deleted")
    print(f"deletion_id: {deletion_id}")
    print(f"evidence_id: {evidence_id}")
    print(f"event_id: {row['event_id']}")
    print(f"source_id_preserved: {row['source_id']}")
    print(f"decision_source: {args.source}")
    print(f"actor_snapshot: {args.actor or 'none'}")
    print("atomic_commit: true")
    return 0


def cmd_delete_event(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    event_id = str(args.event_id)

    with connect(db_path) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            require_schema_v6(conn)

            event = fetch_one(
                conn,
                "SELECT event_id, title FROM events WHERE event_id = ?",
                (event_id,),
            )

            if event is None:
                existing = existing_deletion(conn, "event", event_id)
                if existing is not None:
                    conn.rollback()
                    print_existing_deletion(existing)
                    return 0
                raise CliError(f"event_id not found: {event_id}")

            assessment_count = int(
                fetch_one(
                    conn,
                    "SELECT COUNT(*) AS n FROM assessments WHERE event_id = ?",
                    (event_id,),
                )["n"]
            )
            evidence_count = int(
                fetch_one(
                    conn,
                    "SELECT COUNT(*) AS n FROM evidence_items WHERE event_id = ?",
                    (event_id,),
                )["n"]
            )

            source_ids = {
                int(r["source_id"])
                for r in conn.execute(
                    "SELECT DISTINCT source_id FROM evidence_items WHERE event_id = ?",
                    (event_id,),
                ).fetchall()
            }

            deletion_id = insert_audit(
                conn,
                target_type="event",
                target_id_snapshot=event_id,
                event_id_snapshot=event_id,
                target_label_snapshot=str(event["title"] or event_id),
                source_id_snapshot=None,
                assessment_count=assessment_count,
                evidence_count=evidence_count,
                decision_source=args.source,
                actor_snapshot=args.actor,
            )

            cur = conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
            if cur.rowcount != 1:
                raise CliError(
                    f"expected to delete 1 event row, deleted {cur.rowcount}"
                )

            remaining_assessments = int(
                fetch_one(
                    conn,
                    "SELECT COUNT(*) AS n FROM assessments WHERE event_id = ?",
                    (event_id,),
                )["n"]
            )
            remaining_evidence = int(
                fetch_one(
                    conn,
                    "SELECT COUNT(*) AS n FROM evidence_items WHERE event_id = ?",
                    (event_id,),
                )["n"]
            )
            if remaining_assessments or remaining_evidence:
                raise CliError(
                    "event delete did not cascade all assessments/evidence; rolling back"
                )

            missing_sources = 0
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                preserved = int(
                    fetch_one(
                        conn,
                        f"SELECT COUNT(*) AS n FROM sources WHERE source_id IN ({placeholders})",
                        tuple(sorted(source_ids)),
                    )["n"]
                )
                missing_sources = len(source_ids) - preserved
            if missing_sources:
                raise CliError(
                    f"{missing_sources} source row(s) disappeared during event delete; rolling back"
                )

            validate_integrity(conn)
            conn.commit()

        except Exception:
            conn.rollback()
            raise

    print("OK: PROROK event deleted")
    print(f"deletion_id: {deletion_id}")
    print(f"event_id: {event_id}")
    print(f"assessment_count_deleted: {assessment_count}")
    print(f"evidence_count_deleted: {evidence_count}")
    print(f"source_rows_preserved: {len(source_ids)}")
    print(f"decision_source: {args.source}")
    print(f"actor_snapshot: {args.actor or 'none'}")
    print("atomic_commit: true")
    return 0


def add_common_delete_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        default="telegram",
        choices=SOURCE_CHOICES,
        help="Audit source for the explicit deletion",
    )
    parser.add_argument(
        "--actor",
        help="Optional actor snapshot, e.g. telegram:<sender-id>",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically hard-delete PROROK event/evidence records"
    )
    parser.add_argument("--home", help="PROROK data directory")
    parser.add_argument("--db", help="SQLite DB path")

    sub = parser.add_subparsers(dest="command", required=True)

    event_cmd = sub.add_parser(
        "delete-event",
        help="Delete one event and FK-cascaded assessments/evidence with audit",
    )
    event_cmd.add_argument("event_id")
    add_common_delete_args(event_cmd)
    event_cmd.set_defaults(func=cmd_delete_event)

    evidence_cmd = sub.add_parser(
        "delete-evidence",
        help="Delete one evidence row while preserving its source with audit",
    )
    evidence_cmd.add_argument("evidence_id", type=int)
    add_common_delete_args(evidence_cmd)
    evidence_cmd.set_defaults(func=cmd_delete_evidence)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.IntegrityError as exc:
        print(f"SQLITE_INTEGRITY_ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"SQLITE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
