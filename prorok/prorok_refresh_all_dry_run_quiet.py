#!/usr/bin/env python3
"""Schedule no-write PROROK refresh dry-runs for all active events.

This launcher is intentionally thin: it reads active PROROK event IDs from the
SQLite database and invokes the already-tested single-event quiet refresh launcher
for each event. Jobs are spaced out with incremental `--at` offsets to avoid
starting too many OpenClaw cron jobs at the same time.

For real scheduling runs, the launcher writes only the refresh audit lifecycle:
one refresh_runs batch plus one pending refresh_event_results snapshot per target,
then records cron_id/run_at or schedule_failed. It never writes official PROROK
assessments, evidence_items, sources, or event forecasts. --no-schedule remains a
diagnostic prompt-only mode and does not create collector lifecycle rows.

Each target scheduling attempt is also appended to the legacy JSONL operational
audit log under /data/workspace/prorok by default.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path("/data/workspace/prorok/prorok.sqlite3")
DEFAULT_PROMPT_DIR = Path("/data/workspace/prorok/refresh_prompts")
DEFAULT_AUDIT_LOG = Path("/data/workspace/prorok/refresh_runs.jsonl")
DEFAULT_CHAT_ID = "-1003804919781"
DEFAULT_THREAD_ID = "112"
DEFAULT_START_AT = "2m"
DEFAULT_SPACING_MINUTES = 3
DEFAULT_LIMIT = 50
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_TOOLS = "tavily_search tavily_extract web_search web_fetch read"


@dataclass(frozen=True)
class RefreshTarget:
    event_id: str
    title: str
    status: str
    forecast_horizon: str
    updated_at: str
    baseline_assessment_id: int | None
    baseline_probability: int | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_refresh_script() -> Path:
    code_dir = Path(__file__).resolve().parent
    quiet = code_dir / "prorok_refresh_dry_run_quiet.py"
    if quiet.exists():
        return quiet
    fallback = code_dir / "prorok_refresh_dry_run_cron.py"
    if fallback.exists():
        return fallback
    raise SystemExit(f"PROROK single-event refresh launcher is not available in {code_dir}")


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"PROROK database not found: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def require_refresh_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    current = version["value"] if version else None
    try:
        current_version = int(current) if current is not None else None
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "PROROK schema version must be an integer >= 3 for refresh lifecycle; "
            f"current={current!r}"
        ) from exc

    if current_version is None or current_version < 3:
        raise RuntimeError(
            "PROROK schema v3+ required for refresh lifecycle; "
            f"current={current!r}"
        )

    required_tables = {
        "refresh_runs",
        "refresh_event_results",
        "refresh_candidate_evidence",
    }
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required_tables - tables)
    if missing:
        raise RuntimeError(
            "PROROK refresh tables missing: " + ", ".join(missing)
        )


def load_targets(db_path: Path, status: str, limit: int) -> list[RefreshTarget]:
    status_sql = ""
    params: list[object] = []
    if status != "all":
        status_sql = "WHERE e.status = ?"
        params.append(status)

    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.event_id,
                e.title,
                e.status,
                e.forecast_horizon,
                e.updated_at,
                a.assessment_id AS baseline_assessment_id,
                a.probability_percent AS baseline_probability
            FROM events e
            LEFT JOIN assessments a
              ON a.assessment_id = (
                  SELECT a2.assessment_id
                  FROM assessments a2
                  WHERE a2.event_id = e.event_id
                  ORDER BY a2.assessed_at DESC, a2.assessment_id DESC
                  LIMIT 1
              )
            {status_sql}
            ORDER BY e.updated_at DESC, e.event_id
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

    return [
        RefreshTarget(
            event_id=row["event_id"] or "",
            title=row["title"] or "",
            status=row["status"] or "",
            forecast_horizon=row["forecast_horizon"] or "",
            updated_at=row["updated_at"] or "",
            baseline_assessment_id=row["baseline_assessment_id"],
            baseline_probability=row["baseline_probability"],
        )
        for row in rows
    ]


def create_refresh_batch(
    db_path: Path,
    targets: list[RefreshTarget],
    trigger_source: str,
) -> tuple[int, dict[str, int]]:
    with connect(db_path) as conn:
        require_refresh_schema(conn)
        with conn:
            cur = conn.execute(
                """
                INSERT INTO refresh_runs(
                    mode,
                    trigger_source,
                    scope,
                    status,
                    phase,
                    target_count,
                    scheduled_count
                )
                VALUES('dry_run', ?, 'all', 'running', 'scheduling', ?, 0)
                """,
                (trigger_source, len(targets)),
            )
            refresh_id = int(cur.lastrowid)

            child_ids: dict[str, int] = {}
            for target in targets:
                child = conn.execute(
                    """
                    INSERT INTO refresh_event_results(
                        refresh_id,
                        event_id,
                        event_title_snapshot,
                        baseline_assessment_id,
                        baseline_probability,
                        job_state,
                        outcome
                    )
                    VALUES(?, ?, ?, ?, ?, 'pending', NULL)
                    """,
                    (
                        refresh_id,
                        target.event_id,
                        target.title,
                        target.baseline_assessment_id,
                        target.baseline_probability,
                    ),
                )
                child_ids[target.event_id] = int(child.lastrowid)

    return refresh_id, child_ids


def mark_schedule_result(
    db_path: Path,
    refresh_id: int,
    child_id: int,
    *,
    scheduled: bool,
    cron_id: str = "",
    run_at: str = "",
    error: str = "",
) -> None:
    with connect(db_path) as conn:
        with conn:
            if scheduled:
                conn.execute(
                    """
                    UPDATE refresh_event_results
                    SET job_state = 'scheduled',
                        cron_id = ?,
                        expected_run_at = ?,
                        outcome = NULL,
                        parse_error = NULL
                    WHERE refresh_event_result_id = ?
                      AND refresh_id = ?
                    """,
                    (cron_id, run_at or None, child_id, refresh_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE refresh_event_results
                    SET job_state = 'schedule_failed',
                        outcome = 'error',
                        parse_error = ?
                    WHERE refresh_event_result_id = ?
                      AND refresh_id = ?
                    """,
                    (error[-4000:] or "scheduler did not return cron_id", child_id, refresh_id),
                )


def finalize_scheduling(db_path: Path, refresh_id: int) -> tuple[int, int]:
    with connect(db_path) as conn:
        with conn:
            counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN job_state = 'scheduled' THEN 1 ELSE 0 END) AS scheduled_count,
                    SUM(CASE WHEN job_state = 'schedule_failed' THEN 1 ELSE 0 END) AS failed_count
                FROM refresh_event_results
                WHERE refresh_id = ?
                """,
                (refresh_id,),
            ).fetchone()
            scheduled_count = int(counts["scheduled_count"] or 0)
            failed_count = int(counts["failed_count"] or 0)

            if scheduled_count:
                phase = "waiting"
                status = "running"
                finished_at = None
            else:
                phase = "done"
                status = "failed"
                finished_at = utc_now_iso()

            conn.execute(
                """
                UPDATE refresh_runs
                SET scheduled_count = ?,
                    scheduling_finished_at = ?,
                    phase = ?,
                    status = ?,
                    finished_at = ?,
                    error_count = ?
                WHERE refresh_id = ?
                """,
                (
                    scheduled_count,
                    utc_now_iso(),
                    phase,
                    status,
                    finished_at,
                    failed_count,
                    refresh_id,
                ),
            )

    return scheduled_count, failed_count


def parse_minutes(value: str) -> int:
    text = str(value or "").strip().lower()
    if not text:
        raise argparse.ArgumentTypeError("time offset must not be empty")

    suffixes = ("minutes", "minute", "mins", "min", "m")
    for suffix in suffixes:
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            break
    else:
        number = text

    try:
        minutes = int(number)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"unsupported time offset: {value!r}; use minutes like 2m, 5m, 10"
        ) from exc

    if minutes < 1:
        raise argparse.ArgumentTypeError("time offset must be at least 1 minute")
    return minutes


def minute_offset(minutes: int) -> str:
    return f"{minutes}m"


def run_one(script: Path, target: RefreshTarget, args: argparse.Namespace, at_value: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(script),
        target.event_id,
        "--db",
        str(args.db),
        "--prompt-dir",
        str(args.prompt_dir),
        "--to",
        args.to,
        "--thread-id",
        str(args.thread_id),
        "--at",
        at_value,
        "--agent",
        args.agent,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--tools",
        args.tools,
        "--evidence-limit",
        str(args.evidence_limit),
    ]
    if args.no_schedule:
        cmd.append("--no-schedule")

    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def parse_launcher_output(stdout: str) -> dict[str, str]:
    """Extract stable key/value fields printed by the single-event launcher."""
    fields: dict[str, str] = {}
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"prompt_file", "schedule", "cron_id", "run_at"}:
            fields[key] = value
    return fields


def append_audit_record(audit_log: Path, record: dict[str, object]) -> None:
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    with audit_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Schedule no-write PROROK refresh dry-runs for active events."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to PROROK SQLite database")
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPT_DIR, help="Directory for generated prompt files")
    parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_LOG, help="JSONL audit log for scheduled refresh attempts")
    parser.add_argument("--status", default="active", choices=["active", "paused", "resolved", "archived", "all"])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum number of events to refresh")
    parser.add_argument("--start-at", default=DEFAULT_START_AT, help="First one-shot offset, for example 2m")
    parser.add_argument("--spacing-minutes", type=int, default=DEFAULT_SPACING_MINUTES, help="Minutes between scheduled jobs")
    parser.add_argument("--to", default=DEFAULT_CHAT_ID, help="Telegram chat id for delivery")
    parser.add_argument("--thread-id", default=DEFAULT_THREAD_ID, help="Telegram forum topic thread id")
    parser.add_argument("--agent", default="main", help="OpenClaw agent id")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--tools", default=DEFAULT_TOOLS, help="Tool allow-list for each agent job")
    parser.add_argument("--evidence-limit", type=int, default=12, help="Latest evidence rows to include per event")
    parser.add_argument(
        "--trigger-source",
        default="manual_cli",
        choices=["scheduled", "telegram", "manual_cli", "system"],
        help="Audit provenance for v3 refresh_runs",
    )
    parser.add_argument("--no-schedule", action="store_true", help="Only write prompt files; do not create cron jobs or v3 collector rows")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 2
    if args.spacing_minutes < 1:
        print("--spacing-minutes must be at least 1", file=sys.stderr)
        return 2

    start_minutes = parse_minutes(args.start_at)
    script = resolve_refresh_script()
    targets = load_targets(args.db, args.status, args.limit)

    print("PROROK_REFRESH_ALL_DRY_RUN")
    print(f"db: {args.db}")
    print(f"status_filter: {args.status}")
    print(f"targets: {len(targets)}")
    print(f"limit: {args.limit}")
    print(f"start_at: {minute_offset(start_minutes)}")
    print(f"spacing_minutes: {args.spacing_minutes}")
    print(f"audit_log: {args.audit_log}")
    print(f"schedule: {'skipped (--no-schedule)' if args.no_schedule else 'creating'}")

    if not targets:
        print("result: no matching events")
        return 0

    refresh_id: int | None = None
    child_ids: dict[str, int] = {}
    if not args.no_schedule:
        try:
            refresh_id, child_ids = create_refresh_batch(
                args.db,
                targets,
                args.trigger_source,
            )
        except Exception as exc:
            print(f"refresh_batch_error: {exc}", file=sys.stderr)
            return 1
        print(f"refresh_id: {refresh_id}")
        print(f"trigger_source: {args.trigger_source}")
        print(f"batch_phase: scheduling")

    failures = 0
    for idx, target in enumerate(targets, start=1):
        at_value = minute_offset(start_minutes + ((idx - 1) * args.spacing_minutes))
        print("")
        print(f"{idx}. {target.event_id}")
        print(f"   title: {target.title}")
        print(f"   status: {target.status} | horizon: {target.forecast_horizon or 'n/a'}")
        print(f"   updated_at: {target.updated_at or 'n/a'}")
        print(f"   at: {at_value}")

        proc = run_one(script, target, args, at_value)
        parsed = parse_launcher_output(proc.stdout or "")
        cron_id = parsed.get("cron_id", "")
        run_at = parsed.get("run_at", "")
        target_failed = proc.returncode != 0
        scheduled = bool(cron_id) and not args.no_schedule and not target_failed

        if refresh_id is not None:
            error_text = (proc.stderr or proc.stdout or "")[-4000:]
            try:
                mark_schedule_result(
                    args.db,
                    refresh_id,
                    child_ids[target.event_id],
                    scheduled=scheduled,
                    cron_id=cron_id,
                    run_at=run_at,
                    error=error_text,
                )
            except Exception as exc:
                target_failed = True
                scheduled = False
                print(f"   refresh_batch_update_error: {exc}", file=sys.stderr)

        if proc.stdout:
            for line in proc.stdout.rstrip().splitlines():
                print(f"   {line}")
        if proc.stderr:
            print("   stderr:", file=sys.stderr)
            for line in proc.stderr.rstrip().splitlines():
                print(f"   {line}", file=sys.stderr)
        if not args.no_schedule and not scheduled:
            target_failed = True

        if target_failed:
            if proc.returncode != 0:
                print(f"   result: failed rc={proc.returncode}")
            else:
                print("   result: failed (cron_id not recorded)")
        else:
            print("   result: ok")

        audit_record: dict[str, object] = {
            "timestamp_utc": utc_now_iso(),
            "launcher": "prorok_refresh_all_dry_run_quiet.py",
            "action": "schedule_refresh_dry_run",
            "event_id": target.event_id,
            "title": target.title,
            "status": target.status,
            "forecast_horizon": target.forecast_horizon,
            "updated_at": target.updated_at,
            "index": idx,
            "target_count": len(targets),
            "at_offset": at_value,
            "prompt_file": parsed.get("prompt_file", ""),
            "schedule_output": parsed.get("schedule", ""),
            "cron_id": parsed.get("cron_id", ""),
            "run_at": parsed.get("run_at", ""),
            "no_schedule": bool(args.no_schedule),
            "scheduled": scheduled,
            "refresh_id": refresh_id,
            "refresh_event_result_id": child_ids.get(target.event_id),
            "returncode": proc.returncode,
            "result": "failed" if target_failed else "ok",
            "stderr_tail": (proc.stderr or "")[-2000:],
            "to": args.to,
            "thread_id": str(args.thread_id),
            "agent": args.agent,
        }
        try:
            append_audit_record(args.audit_log, audit_record)
            print(f"   audit_log: appended {args.audit_log}")
        except OSError as exc:
            failures += 1
            print(f"   audit_log_error: {exc}", file=sys.stderr)

        if target_failed:
            failures += 1

    if refresh_id is not None:
        try:
            scheduled_count, schedule_failed_count = finalize_scheduling(
                args.db,
                refresh_id,
            )
            print("")
            print(f"batch_scheduled: {scheduled_count}/{len(targets)}")
            print(f"batch_schedule_failed: {schedule_failed_count}")
            print(f"batch_phase: {'waiting' if scheduled_count else 'done'}")
        except Exception as exc:
            print(f"refresh_batch_finalize_error: {exc}", file=sys.stderr)
            return 1

    print("")
    print(f"completed: {len(targets) - failures}/{len(targets)}")
    if failures:
        print(f"failures: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
