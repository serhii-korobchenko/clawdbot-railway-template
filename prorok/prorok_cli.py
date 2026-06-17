#!/usr/bin/env python3
"""Small operational CLI for the PROROK forecasting database.

The CLI is intentionally dependency-free and uses only Python stdlib so it can
run inside the Railway/OpenClaw container. It is the first operational layer for
manual checks before Telegram commands and cron automation are connected.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten
from typing import Iterable, Sequence


DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"


TEST_EVENT_ID = "ru_capture_ukraine_oblast_center_2026"
TEST_EVENT_TITLE = "РФ захопить обласний центр України у 2026 році"
TEST_EVENT_QUESTION = (
    "Чи зможе Росія захопити один з обласних центрів України та зберегти "
    "контроль над ним протягом 2026 року?"
)
TEST_EVENT_FORECAST_HORIZON = "2026-12-31"
TEST_EVENT_CRITERIA = {
    "positive_resolution": (
        "Повна втрата Україною контролю над будь-яким обласним центром, який "
        "контролювався Україною станом на 2026-01-01."
    ),
    "control_duration": "7 діб поспіль або більше.",
    "confirmation": (
        "Мінімум два незалежні джерела: міжнародні спостерігачі, міжнародні "
        "ЗМІ або офіційні повідомлення української влади."
    ),
}
TEST_EVENT_TAGS = ["ukraine", "russia", "war", "oblast-center", "2026"]


class CliError(RuntimeError):
    """Raised for user-facing CLI errors."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_prorok_home(explicit_home: str | None) -> Path:
    if explicit_home:
        return Path(explicit_home).expanduser().resolve()

    env_home = os.getenv("PROROK_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_path:
        return Path(volume_path).expanduser().resolve() / "prorok"

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "workspace" / "prorok"

    return Path(DEFAULT_PROROK_HOME).resolve()


def resolve_db_path(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db).expanduser().resolve()

    env_db = os.getenv("PROROK_DB")
    if env_db:
        return Path(env_db).expanduser().resolve()

    return resolve_prorok_home(args.home) / DEFAULT_DB_NAME


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise CliError(f"PROROK DB not found: {db_path}. Run prorok_init_db.py first.")

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def print_row(label: str, value: object | None) -> None:
    if value is None:
        value = ""
    print(f"{label}: {value}")


def print_section(title: str) -> None:
    print("")
    print(f"=== {title} ===")


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def count_table(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


def cmd_health(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args)
    with connect(db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

        print("PROROK DB health")
        print_row("DB_PATH", db_path)
        print_row("DB_SIZE_BYTES", db_path.stat().st_size)
        print_row("journal_mode", journal_mode)
        print_row("foreign_keys", foreign_keys)

        print_section("Counts")
        for table in ["events", "assessments", "sources", "evidence_items", "runs", "meta"]:
            print_row(table, count_table(conn, table))

        print_section("Meta")
        for row in conn.execute("SELECT key, value, updated_at FROM meta ORDER BY key"):
            value = row["value"]
            if value and len(value) > 140:
                value = value[:137] + "..."
            print(f"- {row['key']}: {value}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args)
    status = args.status

    sql = """
        SELECT
            event_id,
            title,
            status,
            forecast_horizon,
            probability_percent,
            probability_label,
            confidence,
            assessed_at,
            delta_from_previous
        FROM latest_event_state
    """
    params: list[object] = []
    if status != "all":
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, event_id"

    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    print(f"PROROK events: {len(rows)}")
    if not rows:
        print("No events found.")
        return 0

    for i, row in enumerate(rows, start=1):
        probability = "n/a"
        if row["probability_percent"] is not None:
            probability = f"{row['probability_percent']}% — {row['probability_label']}"
            if row["delta_from_previous"] is not None:
                probability += f" ({row['delta_from_previous']:+d})"

        print("")
        print(f"{i}. {row['title']}")
        print(f"   event_id: {row['event_id']}")
        print(f"   status: {row['status']} | horizon: {row['forecast_horizon'] or ''}")
        print(f"   latest: {probability} | confidence: {row['confidence'] or 'n/a'}")
        print(f"   assessed_at: {row['assessed_at'] or 'n/a'}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args)
    event_id = args.event_id

    with connect(db_path) as conn:
        event = fetch_one(conn, "SELECT * FROM latest_event_state WHERE event_id = ?", (event_id,))
        if event is None:
            raise CliError(f"Event not found: {event_id}")

        evidence_rows = conn.execute(
            """
            SELECT
                ei.direction,
                ei.strength,
                ei.summary,
                ei.relevance,
                ei.credibility,
                ei.created_at,
                s.title AS source_title,
                s.domain,
                s.url
            FROM evidence_items ei
            JOIN sources s ON s.source_id = ei.source_id
            WHERE ei.event_id = ?
            ORDER BY ei.created_at DESC, ei.evidence_id DESC
            LIMIT ?
            """,
            (event_id, args.evidence_limit),
        ).fetchall()

    print(f"PROROK event: {event['event_id']}")
    print_row("Title", event["title"])
    print_row("Status", event["status"])
    print_row("Forecast horizon", event["forecast_horizon"])
    print_row("Tags", event["tags"])
    print_row("Question", event["question"])
    print_row("Decision criteria", event["decision_criteria"])

    print_section("Latest assessment")
    if event["assessment_id"] is None:
        print("No assessment yet.")
    else:
        print_row("Assessed at", event["assessed_at"])
        print_row("Probability", f"{event['probability_percent']}%")
        print_row("Band", event["probability_band"])
        print_row("Label", event["probability_label"])
        print_row("Confidence", event["confidence"])
        print_row("Delta", event["delta_from_previous"])
        print_row("Rationale", event["rationale"])

    print_section(f"Evidence latest {len(evidence_rows)}")
    if not evidence_rows:
        print("No evidence items yet.")
    else:
        for row in evidence_rows:
            print(f"- [{row['direction']}/{row['strength'] or 'n/a'}] {row['summary']}")
            print(f"  relevance={row['relevance']} credibility={row['credibility']} created_at={row['created_at']}")
            print(f"  source={row['source_title'] or row['domain'] or 'n/a'}")
            print(f"  url={row['url']}")
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args)
    event_id = args.event_id

    with connect(db_path) as conn:
        event = fetch_one(conn, "SELECT event_id, title FROM events WHERE event_id = ?", (event_id,))
        if event is None:
            raise CliError(f"Event not found: {event_id}")
        rows = conn.execute(
            """
            SELECT
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
            (event_id,),
        ).fetchall()

    print(f"PROROK trend: {event['title']}")
    print_row("event_id", event_id)
    if not rows:
        print("No assessments yet.")
        return 0

    for row in rows:
        delta = ""
        if row["delta_from_previous"] is not None:
            delta = f" delta={row['delta_from_previous']:+d}"
        rationale = shorten(row["rationale"] or "", width=args.rationale_width, placeholder="...")
        print(
            f"- {row['assessed_at']}: {row['probability_percent']}% "
            f"({row['probability_label']}, confidence={row['confidence'] or 'n/a'}{delta})"
        )
        if rationale:
            print(f"  {rationale}")
    return 0


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


def create_run(conn: sqlite3.Connection, run_type: str, notes: str | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(run_type, status, notes)
        VALUES (?, 'running', ?)
        """,
        (run_type, notes),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    events_processed: int = 0,
    new_sources_found: int = 0,
    errors: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET finished_at = ?,
            status = ?,
            events_processed = ?,
            new_sources_found = ?,
            errors = ?
        WHERE run_id = ?
        """,
        (utc_now(), status, events_processed, new_sources_found, errors, run_id),
    )


def cmd_add_test_event(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args)
    now = utc_now()
    criteria = json.dumps(TEST_EVENT_CRITERIA, ensure_ascii=False, indent=2)
    tags = json.dumps(TEST_EVENT_TAGS, ensure_ascii=False)

    with connect(db_path) as conn:
        try:
            run_id = create_run(conn, "manual_cli", "Add PROROK test event from screenshot example")

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
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    title = excluded.title,
                    question = excluded.question,
                    status = 'active',
                    forecast_horizon = excluded.forecast_horizon,
                    decision_criteria = excluded.decision_criteria,
                    tags = excluded.tags,
                    source_image_note = excluded.source_image_note
                """,
                (
                    TEST_EVENT_ID,
                    TEST_EVENT_TITLE,
                    TEST_EVENT_QUESTION,
                    TEST_EVENT_FORECAST_HORIZON,
                    criteria,
                    tags,
                    "Initial event created from the supplied PROROK screenshot example.",
                    now,
                    now,
                ),
            )

            prev = latest_probability(conn, TEST_EVENT_ID)
            should_add_assessment = args.add_assessment or prev is None

            if should_add_assessment:
                probability = args.probability
                delta = None if prev is None else probability - prev
                conn.execute(
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
                        TEST_EVENT_ID,
                        run_id,
                        now,
                        probability,
                        args.band,
                        args.label,
                        args.confidence,
                        delta,
                        args.rationale,
                    ),
                )

            finish_run(conn, run_id, status="completed", events_processed=1, new_sources_found=0)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - mark run failed before surfacing error.
            if "run_id" in locals():
                finish_run(conn, run_id, status="failed", errors=str(exc))
                conn.commit()
            raise

    print("OK: test event saved")
    print_row("event_id", TEST_EVENT_ID)
    print_row("db", db_path)
    print_row("assessment_added", should_add_assessment)
    print_row("probability", f"{args.probability}%" if should_add_assessment else "unchanged")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PROROK operational CLI")
    parser.add_argument("--home", help="PROROK data directory. Defaults to PROROK_HOME or Railway volume path.")
    parser.add_argument("--db", help="SQLite DB path. Defaults to PROROK_DB or <home>/prorok.sqlite3.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Check PROROK database health")
    health.set_defaults(func=cmd_health)

    list_cmd = subparsers.add_parser("list", help="List events with latest assessment")
    list_cmd.add_argument("--status", default="active", choices=["active", "paused", "resolved", "archived", "all"])
    list_cmd.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Show a full event card")
    show.add_argument("event_id")
    show.add_argument("--evidence-limit", type=int, default=20)
    show.set_defaults(func=cmd_show)

    trend = subparsers.add_parser("trend", help="Show assessment history for an event")
    trend.add_argument("event_id")
    trend.add_argument("--rationale-width", type=int, default=180)
    trend.set_defaults(func=cmd_trend)

    add_test = subparsers.add_parser("add-test-event", help="Insert/update the initial screenshot-based test event")
    add_test.add_argument("--probability", type=int, default=25)
    add_test.add_argument("--band", default="25-35%")
    add_test.add_argument("--label", default="Малоймовірно")
    add_test.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    add_test.add_argument(
        "--rationale",
        default=(
            "Початкова тестова оцінка для перевірки структури БД і CLI. "
            "Повноцінна оцінка має бути сформована після web-пошуку індикаторів і контріндикаторів."
        ),
    )
    add_test.add_argument(
        "--add-assessment",
        action="store_true",
        help="Add a new assessment even if this event already has one.",
    )
    add_test.set_defaults(func=cmd_add_test_event)

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
