#!/usr/bin/env python3
"""Read-only post-run freshness audit for an OpenClaw PROROK session JSONL.

This is a standalone diagnostic for cron runs outside refresh_event_results.
The tracked collector already enforces the full v8 gate. No database writes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import prorok_refresh_collector_v6 as v6
import prorok_refresh_collector as collector


def wait_for_transcript(
    state_dir: Path, cron_id: str, timeout_seconds: float = 360,
    *, min_run_at_ms: int | None = None, expected_session_id: str | None = None,
) -> Path:
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = collector.find_latest_finished_run(state_dir, cron_id)
        if run is not None and (min_run_at_ms is None or run.run_at_ms >= min_run_at_ms) and (expected_session_id is None or run.session_id == expected_session_id):
            if run.status != "ok":
                raise ValueError("cron execution failed")
            if not run.session_id:
                raise ValueError("completed cron has no session ID")
            return collector.resolve_session_transcript_path(state_dir, run.session_id, run.session_key)
        if time.monotonic() >= deadline:
            raise TimeoutError("cron completion timed out")
        time.sleep(2)



def check_freshness(session_path: Path) -> tuple[bool, str, int]:
    searches, results, verifications = v6._extract_trace(session_path)
    core = searches[:3]
    if len(core) != 3:
        return False, f"expected 3 search calls; observed {len(core)}", 0
    if any(call["tool"] != "tavily_search" for call in core):
        return False, "first three searches must use tavily_search", 0
    targets, errors = v6._undated_verification_targets(core, results)
    if errors:
        return False, "; ".join(errors), len(targets)
    verified_after: dict[str, int] = {}
    for call in verifications:
        sequence = int(call["sequence"])
        for url in v6._verification_urls(call["args"]):
            verified_after[url] = max(verified_after.get(url, 0), sequence)
    missing = sorted(url for url, boundary in targets.items()
                     if verified_after.get(url, 0) <= boundary)
    if missing:
        # Avoid exposing source URLs in routine diagnostic output.
        return False, f"missing freshness verification for {len(missing)} required URL(s)", len(targets)
    return True, "all required undated URLs verified after search", len(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session-jsonl", type=Path)
    source.add_argument("--cron-id")
    parser.add_argument("--state-dir", type=Path, default=Path(collector.DEFAULT_STATE_DIR))
    parser.add_argument("--timeout-seconds", type=float, default=360)
    parser.add_argument("--min-run-at-ms", type=int, default=None)
    parser.add_argument("--expected-session-id", default=None)
    args = parser.parse_args(argv)
    try:
        path = (wait_for_transcript(
                    args.state_dir, args.cron_id, args.timeout_seconds,
                    min_run_at_ms=args.min_run_at_ms,
                    expected_session_id=args.expected_session_id,
                )
                if args.cron_id else args.session_jsonl)
        valid, reason, required = check_freshness(path)
    except (OSError, ValueError, TimeoutError) as exc:
        print(f"freshness_check: FAIL; unreadable transcript: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"freshness_check: {'PASS' if valid else 'FAIL'}")
    print(f"required_unique_urls: {required}")
    print(f"reason: {reason}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
