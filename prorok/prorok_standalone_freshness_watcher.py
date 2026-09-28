#!/usr/bin/env python3
"""One polling pass for standalone PROROK cron freshness checks (no DB writes)."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import prorok_refresh_collector as collector
import prorok_refresh_session_freshness_check as freshness
from prorok_standalone_freshness_registry import (
    DEFAULT_REGISTRY, record_result, registered_jobs,
)


def inspect_job(state_dir: Path, job: dict, *, now_ms: int | None = None,
                transcript_grace_ms: int = 120_000) -> tuple[str, str, int] | None:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    cron_id = job["cron_id"]
    expected = job.get("expected_run_at_ms")
    lower_bound = int(job["created_at_ms"])
    runs = []
    for item in collector.read_jsonl(state_dir / "cron" / "runs" / f"{cron_id}.jsonl"):
        run = collector.parse_cron_run(cron_id, item)
        if run is not None and run.run_at_ms >= lower_bound:
            runs.append(run)
    run = max(runs, key=lambda item: item.run_at_ms, default=None)
    deadline = max(int(expected or 0), lower_bound) + transcript_grace_ms
    if run is None:
        if now < deadline:
            return None
        return "ERROR", "cron completion timed out", 0
    if run.status != "ok":
        return "ERROR", "cron execution failed", 0
    if not run.session_id:
        return "ERROR", "completed cron has no session ID", 0
    try:
        path = collector.resolve_session_transcript_path(state_dir, run.session_id, run.session_key)
        valid, reason, required = freshness.check_freshness(path)
    except (OSError, ValueError) as exc:
        if now < max(deadline, run.run_at_ms + transcript_grace_ms):
            return None
        return "ERROR", f"transcript unavailable or invalid: {type(exc).__name__}", 0
    return ("PASS" if valid else "FAIL"), reason, required


def collect_once(registry: Path, state_dir: Path, *, now_ms: int | None = None,
                 transcript_grace_ms: int = 120_000) -> dict[str, int]:
    counts = {"pending": 0, "PASS": 0, "FAIL": 0, "ERROR": 0}
    for job in registered_jobs(registry).values():
        if "result" in job:
            continue
        outcome = inspect_job(state_dir, job, now_ms=now_ms,
                              transcript_grace_ms=transcript_grace_ms)
        if outcome is None:
            counts["pending"] += 1
            continue
        status, reason, required = outcome
        record_result(registry, cron_id=job["cron_id"], status=status,
                      reason=reason, required=required)
        counts[status] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--state-dir", type=Path, default=Path(collector.DEFAULT_STATE_DIR))
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be positive")
    while True:
        print(collect_once(args.registry, args.state_dir), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
