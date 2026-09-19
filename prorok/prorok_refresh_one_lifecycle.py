#!/usr/bin/env python3
"""Schedule one PROROK refresh through the audited collector lifecycle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import prorok_refresh_all_dry_run_quiet as batch


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Schedule one no-write PROROK refresh with collector lifecycle tracking."
    )
    parser.add_argument("event_id")
    parser.add_argument("--db", type=Path, default=batch.DEFAULT_DB_PATH)
    parser.add_argument("--prompt-dir", type=Path, default=batch.DEFAULT_PROMPT_DIR)
    parser.add_argument("--audit-log", type=Path, default=batch.DEFAULT_AUDIT_LOG)
    parser.add_argument("--to", default=batch.DEFAULT_CHAT_ID)
    parser.add_argument("--thread-id", default=batch.DEFAULT_THREAD_ID)
    parser.add_argument("--at", default=batch.DEFAULT_START_AT)
    parser.add_argument("--agent", default="main")
    parser.add_argument("--timeout-seconds", type=int, default=batch.DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--tools", default=batch.DEFAULT_TOOLS)
    parser.add_argument("--evidence-limit", type=int, default=12)
    parser.add_argument(
        "--trigger-source",
        default="manual_cli",
        choices=["scheduled", "telegram", "manual_cli", "system"],
    )
    parser.add_argument("--no-schedule", action="store_true")
    return parser.parse_args(argv)


def load_exact_target(db: Path, event_id: str) -> batch.RefreshTarget | None:
    # Use the batch launcher's canonical snapshot query so baseline semantics stay identical.
    targets = batch.load_targets(db, "all", 100000)
    for target in targets:
        if target.event_id == event_id:
            return target
    return None


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        at_minutes = batch.parse_minutes(args.at)
    except argparse.ArgumentTypeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    target = load_exact_target(args.db, args.event_id)
    if target is None:
        print(f"event_id not found: {args.event_id}", file=sys.stderr)
        return 2

    script = batch.resolve_refresh_script()
    at_value = batch.minute_offset(at_minutes)

    # batch.run_one expects the same namespace fields as the batch CLI.
    args.start_at = at_value
    args.spacing_minutes = 1
    args.limit = 1

    print("PROROK_REFRESH_ONE_DRY_RUN")
    print(f"db: {args.db}")
    print(f"event_id: {target.event_id}")
    print(f"at: {at_value}")
    print(f"schedule: {'skipped (--no-schedule)' if args.no_schedule else 'creating'}")

    if args.no_schedule:
        proc = batch.run_one(script, target, args, at_value)
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        return proc.returncode

    try:
        refresh_id, child_ids = batch.create_refresh_batch(
            args.db,
            [target],
            args.trigger_source,
        )
    except Exception as exc:
        print(f"refresh_batch_error: {exc}", file=sys.stderr)
        return 1

    child_id = child_ids[target.event_id]
    print(f"refresh_id: {refresh_id}")
    print(f"refresh_event_result_id: {child_id}")
    print(f"trigger_source: {args.trigger_source}")

    proc = batch.run_one(script, target, args, at_value)
    parsed = batch.parse_launcher_output(proc.stdout or "")
    cron_id = parsed.get("cron_id", "")
    run_at = parsed.get("run_at", "")
    scheduled = proc.returncode == 0 and bool(cron_id)
    error_text = (proc.stderr or proc.stdout or "")[-4000:]

    try:
        batch.mark_schedule_result(
            args.db,
            refresh_id,
            child_id,
            scheduled=scheduled,
            cron_id=cron_id,
            run_at=run_at,
            error=error_text,
        )
        scheduled_count, failed_count = batch.finalize_scheduling(args.db, refresh_id)
    except Exception as exc:
        print(f"refresh_batch_update_error: {exc}", file=sys.stderr)
        return 1

    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)

    print(f"scheduled_count: {scheduled_count}")
    print(f"failed_count: {failed_count}")
    if not scheduled:
        print("result: failed", file=sys.stderr)
        return proc.returncode or 1

    print("result: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
