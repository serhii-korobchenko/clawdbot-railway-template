#!/usr/bin/env python3
"""PROROK event intake CLI.

Dependency-free helper for adding and reading forecast events extracted from
Telegram screenshots or other inputs. It writes only to the SQLite database; it
is safe to run in the Railway/OpenClaw container.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"
VALID_STATUSES = ("active", "paused", "resolved", "archived")


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


def normalize_tags(tags: str | None) -> str | None:
    if tags is None:
        return None
    items = [item.strip() for item in tags.split(",") if item.strip()]
    return json.dumps(items, ensure_ascii=False)


def normalize_criteria(criteria: str | None, criteria_json: str | None) -> str | None:
    if criteria_json:
        try:
            parsed = json.loads(criteria_json)
        except json.JSONDecodeError as exc:
            raise CliError(f"Invalid --criteria-json: {exc}") from exc
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    return criteria


def validate_event_args(args: argparse.Namespace) -> None:
    for attr in ["event_id", "title", "question"]:
        value = getattr(args, attr)
        if not value or not value.strip():
            raise CliError(f"--{attr.replace('_', '-')} cannot be empty")
    if args.status not in VALID_STATUSES:
        raise CliError(f"--status must be one of: {', '.join(VALID_STATUSES)}")


def cmd_add_event(args: argparse.Namespace) -> int:
    validate_event_args(args)
    db_path = resolve_db(args)
    now = utc_now()
    criteria = normalize_criteria(args.criteria, args.criteria_json)
    tags = normalize_tags(args.tags)

    with connect(db_path) as conn:
        existed = fetch_one(conn, "SELECT event_id FROM events WHERE event_id = ?", (args.event_id,)) is not None
        conn.execute(
            """
            INSERT INTO events(
                event_id,
                title,
                question,
                status,
                forecast_horizon,
                decision_criteria,
                tags,
                source_image_note,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                title = excluded.title,
                question = excluded.question,
                status = excluded.status,
                forecast_horizon = excluded.forecast_horizon,
                decision_criteria = excluded.decision_criteria,
                tags = excluded.tags,
                source_image_note = excluded.source_image_note
            """,
            (
                args.event_id.strip(),
                args.title.strip(),
                args.question.strip(),
                args.status,
                args.forecast_horizon,
                criteria,
                tags,
                args.source_image_note,
                now,
                now,
            ),
        )
        conn.commit()

    print("OK: event saved")
    print(f"event_id: {args.event_id.strip()}")
    print(f"created_new: {not existed}")
    print(f"status: {args.status}")
    print(f"forecast_horizon: {args.forecast_horizon or 'n/a'}")
    print(f"db: {db_path}")
    return 0


def cmd_list_events(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    params: list[object] = []
    status_sql = ""
    if args.status != "all":
        status_sql = "WHERE status = ?"
        params.append(args.status)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                event_id,
                title,
                status,
                forecast_horizon,
                probability_percent,
                probability_label,
                confidence,
                assessed_at,
                updated_at
            FROM latest_event_state
            {status_sql}
            ORDER BY updated_at DESC, event_id
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
    print(f"PROROK events: {len(rows)}")
    for idx, row in enumerate(rows, start=1):
        latest = "n/a"
        if row["probability_percent"] is not None:
            latest = f"{row['probability_percent']}% — {row['probability_label']}"
        print("")
        print(f"{idx}. {row['title']}")
        print(f"   event_id: {row['event_id']}")
        print(f"   status: {row['status']} | horizon: {row['forecast_horizon'] or 'n/a'}")
        print(f"   latest: {latest} | confidence: {row['confidence'] or 'n/a'}")
        print(f"   assessed_at: {row['assessed_at'] or 'n/a'} | updated_at: {row['updated_at']}")
    return 0


def cmd_show_event(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            """
            SELECT
                e.event_id,
                e.title,
                e.question,
                e.status,
                e.forecast_horizon,
                e.decision_criteria,
                e.tags,
                e.source_image_note,
                e.created_at,
                e.updated_at,
                a.assessment_id,
                a.assessed_at,
                a.probability_percent,
                a.probability_band,
                a.probability_label,
                a.confidence,
                a.delta_from_previous,
                a.rationale
            FROM events e
            LEFT JOIN assessments a
              ON a.assessment_id = (
                SELECT assessment_id
                FROM assessments
                WHERE event_id = e.event_id
                ORDER BY assessed_at DESC, assessment_id DESC
                LIMIT 1
              )
            WHERE e.event_id = ?
            """,
            (args.event_id,),
        )
        if row is None:
            raise CliError(f"Event not found: {args.event_id}")
    print(f"PROROK event: {row['event_id']}")
    print(f"title: {row['title']}")
    print(f"status: {row['status']}")
    print(f"forecast_horizon: {row['forecast_horizon'] or 'n/a'}")
    print(f"question: {row['question']}")
    print(f"decision_criteria: {row['decision_criteria'] or 'n/a'}")
    print(f"tags: {row['tags'] or '[]'}")
    print(f"source_image_note: {row['source_image_note'] or 'n/a'}")
    print(f"created_at: {row['created_at']}")
    print(f"updated_at: {row['updated_at']}")
    print("")
    print("Latest assessment:")
    if row["assessment_id"] is None:
        print("  n/a")
    else:
        print(f"  assessed_at: {row['assessed_at']}")
        print(f"  probability: {row['probability_percent']}%")
        print(f"  band: {row['probability_band']}")
        print(f"  label: {row['probability_label']}")
        print(f"  confidence: {row['confidence']}")
        print(f"  delta_from_previous: {row['delta_from_previous'] if row['delta_from_previous'] is not None else 'n/a'}")
        print(f"  rationale: {row['rationale']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PROROK event intake CLI")
    parser.add_argument("--home", help="PROROK data directory")
    parser.add_argument("--db", help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-event", help="Add or update a PROROK forecast event")
    add.add_argument("--event-id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--question", required=True)
    add.add_argument("--status", default="active", choices=VALID_STATUSES)
    add.add_argument("--forecast-horizon")
    add.add_argument("--criteria")
    add.add_argument("--criteria-json")
    add.add_argument("--tags", help="Comma-separated tags, e.g. ukraine,war,2026")
    add.add_argument("--source-image-note")
    add.set_defaults(func=cmd_add_event)

    list_cmd = sub.add_parser("list-events", help="List PROROK events")
    list_cmd.add_argument("--status", default="active", choices=[*VALID_STATUSES, "all"])
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.set_defaults(func=cmd_list_events)

    show = sub.add_parser("show-event", help="Show one PROROK event")
    show.add_argument("event_id")
    show.set_defaults(func=cmd_show_event)
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
