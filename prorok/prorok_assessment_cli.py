#!/usr/bin/env python3
"""PROROK assessment CLI.

Focused helper for writing and reading probability assessments for any PROROK
event. This is intended for daily search/evaluation scripts and manual runtime
checks before Telegram commands are connected.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten
from typing import Iterable, Sequence

DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"
VALID_CONFIDENCE = ("low", "medium", "high")


class CliError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
    return resolve_home(args.home) / DEFAULT_DB_NAME


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise CliError(f"PROROK DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def create_run(conn: sqlite3.Connection, notes: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs(run_type, status, notes) VALUES ('manual_cli', 'running', ?)",
        (notes,),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    events_processed: int = 1,
    errors: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET finished_at = ?, status = ?, events_processed = ?, errors = ?
        WHERE run_id = ?
        """,
        (utc_now(), status, events_processed, errors, run_id),
    )


def latest_probability(conn: sqlite3.Connection, event_id: str) -> int | None:
    row = fetch_one(
        conn,
        """
        SELECT probability_percent
        FROM assessments
        WHERE event_id = ?
        ORDER BY assessed_at DESC, assessment_id DESC
        LIMIT 1
        """,
        (event_id,),
    )
    return None if row is None else int(row["probability_percent"])


def validate_assessment_args(args: argparse.Namespace) -> None:
    if not 0 <= args.probability <= 100:
        raise CliError("--probability must be between 0 and 100")
    if not args.band.strip():
        raise CliError("--band cannot be empty")
    if not args.label.strip():
        raise CliError("--label cannot be empty")
    if args.confidence not in VALID_CONFIDENCE:
        raise CliError(f"--confidence must be one of: {', '.join(VALID_CONFIDENCE)}")
    if not args.rationale.strip():
        raise CliError("--rationale cannot be empty")


def cmd_add_assessment(args: argparse.Namespace) -> int:
    validate_assessment_args(args)
    db_path = resolve_db(args)
    now = utc_now()
    with connect(db_path) as conn:
        run_id = None
        try:
            event = fetch_one(conn, "SELECT event_id, title FROM events WHERE event_id = ?", (args.event_id,))
            if event is None:
                raise CliError(f"Event not found: {args.event_id}")

            previous = latest_probability(conn, args.event_id)
            delta = None if previous is None else args.probability - previous
            run_id = create_run(conn, "Add PROROK probability assessment from CLI")

            cur = conn.execute(
                """
                INSERT INTO assessments(
                    event_id,
                    run_id,
                    assessed_at,
                    probability_percent,
                    probability_band,
                    probability_label,
                    confidence,
                    delta_from_previous,
                    rationale
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    args.event_id,
                    run_id,
                    now,
                    args.probability,
                    args.band,
                    args.label,
                    args.confidence,
                    delta,
                    args.rationale,
                ),
            )
            finish_run(conn, run_id, "completed", 1)
            conn.commit()
        except Exception as exc:
            if run_id is not None:
                finish_run(conn, run_id, "failed", 1, str(exc))
                conn.commit()
            raise

    print("OK: assessment added")
    print(f"assessment_id: {cur.lastrowid}")
    print(f"event_id: {args.event_id}")
    print(f"title: {event['title']}")
    print(f"probability: {args.probability}%")
    print(f"band: {args.band}")
    print(f"label: {args.label}")
    print(f"confidence: {args.confidence}")
    print(f"previous_probability: {previous if previous is not None else 'n/a'}")
    print(f"delta_from_previous: {delta if delta is not None else 'n/a'}")
    print(f"assessed_at: {now}")
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    with connect(db_path) as conn:
        event = fetch_one(
            conn,
            """
            SELECT
                event_id,
                title,
                status,
                forecast_horizon,
                probability_percent,
                probability_band,
                probability_label,
                confidence,
                delta_from_previous,
                assessed_at,
                rationale
            FROM latest_event_state
            WHERE event_id = ?
            """,
            (args.event_id,),
        )
        if event is None:
            raise CliError(f"Event not found: {args.event_id}")

    print(f"PROROK latest assessment: {event['title']}")
    print(f"event_id: {event['event_id']}")
    print(f"status: {event['status']} | horizon: {event['forecast_horizon'] or 'n/a'}")
    if event["probability_percent"] is None:
        print("No assessment yet.")
        return 0
    print(f"assessed_at: {event['assessed_at']}")
    print(f"probability: {event['probability_percent']}%")
    print(f"band: {event['probability_band']}")
    print(f"label: {event['probability_label']}")
    print(f"confidence: {event['confidence']}")
    print(f"delta_from_previous: {event['delta_from_previous'] if event['delta_from_previous'] is not None else 'n/a'}")
    print(f"rationale: {event['rationale']}")
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    with connect(db_path) as conn:
        event = fetch_one(conn, "SELECT event_id, title FROM events WHERE event_id = ?", (args.event_id,))
        if event is None:
            raise CliError(f"Event not found: {args.event_id}")
        rows = conn.execute(
            """
            SELECT
                assessment_id,
                assessed_at,
                probability_percent,
                probability_band,
                probability_label,
                confidence,
                delta_from_previous,
                rationale
            FROM assessment_history
            WHERE event_id = ?
            ORDER BY assessed_at ASC, assessment_id ASC
            """,
            (args.event_id,),
        ).fetchall()

    print(f"PROROK assessment trend: {event['title']}")
    print(f"event_id: {args.event_id}")
    print(f"rows: {len(rows)}")
    for row in rows:
        delta = "n/a" if row["delta_from_previous"] is None else f"{row['delta_from_previous']:+d}"
        rationale = shorten(row["rationale"] or "", width=args.rationale_width, placeholder="...")
        print("")
        print(f"assessment_id: {row['assessment_id']}")
        print(f"assessed_at: {row['assessed_at']}")
        print(f"probability: {row['probability_percent']}% | {row['probability_label']} | delta: {delta}")
        print(f"band: {row['probability_band']} | confidence: {row['confidence']}")
        if rationale:
            print(f"rationale: {rationale}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PROROK assessment CLI")
    parser.add_argument("--home", help="PROROK data directory")
    parser.add_argument("--db", help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-assessment", help="Add a probability assessment for an event")
    add.add_argument("event_id")
    add.add_argument("--probability", type=int, required=True)
    add.add_argument("--band", required=True)
    add.add_argument("--label", required=True)
    add.add_argument("--confidence", default="medium", choices=VALID_CONFIDENCE)
    add.add_argument("--rationale", required=True)
    add.set_defaults(func=cmd_add_assessment)

    latest = sub.add_parser("latest", help="Show the latest assessment for an event")
    latest.add_argument("event_id")
    latest.set_defaults(func=cmd_latest)

    trend = sub.add_parser("trend", help="Show assessment history for an event")
    trend.add_argument("event_id")
    trend.add_argument("--rationale-width", type=int, default=180)
    trend.set_defaults(func=cmd_trend)
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
