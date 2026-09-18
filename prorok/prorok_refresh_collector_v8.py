#!/usr/bin/env python3
"""PROROK refresh collector v8 with universal deterministic search-quality gating.

v8 applies the existing v7 deterministic Tavily/search-quality gate to every
successfully parsed refresh result before downstream collection, including
candidate-positive runs.

Pipeline:
    transcript -> parse -> deterministic v7 search gate -> downstream collector
    -> candidate quarantine / recommendation persistence

This closes the positive-path gap where candidate-bearing refreshes previously
delegated to the older quarantine path before receiving the full v7 gate.

Safety invariants:
- invalid search quality fails closed before candidate rows are written;
- official events, assessments, evidence_items, and sources are never written;
- candidate quarantine rules remain owned by the existing collector stack;
- no schema migration is required; v7 search-quality audit fields are reused.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

import prorok_refresh_collector_v7 as v7

COLLECTOR_VERSION = "8"


def _evaluate_v8_search_quality(
    session_path: Path,
    expected_time_range: str | None,
) -> tuple[bool, str, list[dict[str, Any]]]:
    """Run the v7 deterministic gate for any parsed refresh outcome."""
    valid, v7_reason, calls = v7._evaluate_v7_search_quality(
        session_path,
        expected_time_range,
    )

    if not valid:
        return (
            False,
            "v8 search protocol failed: " + v7_reason,
            calls,
        )

    return (
        True,
        "ok v8: deterministic v7 gate passed for parsed refresh; " + v7_reason,
        calls,
    )


def collect_one_v8(
    conn: sqlite3.Connection,
    state_dir: Path,
    row: sqlite3.Row,
) -> str:
    """Gate every successfully parsed refresh before any downstream collection."""
    if not v7.v6.v5.v4.search_quality_schema_available(conn):
        return v7.collect_one_v7(conn, state_dir, row)

    cron_id = row["cron_id"]
    if not cron_id:
        return v7.collect_one_v7(conn, state_dir, row)

    run = v7.v6.v5.v4.base.find_latest_finished_run(state_dir, cron_id)
    if run is None or run.status not in {"ok", "success", "completed"}:
        return v7.collect_one_v7(conn, state_dir, row)

    if not run.session_id:
        return v7.collect_one_v7(conn, state_dir, row)

    try:
        session_path = v7.v6.v5.v4.base.resolve_session_transcript_path(
            state_dir,
            run.session_id,
            run.session_key,
        )
        transcript = v7.v6.v5.v4.base.extract_final_assistant_text(session_path)
        v7.v6.v5.v4.parse_refresh_report(
            transcript,
            expected_event_id=row["event_id"],
            expected_baseline_probability=row["baseline_probability"],
        )
    except (
        FileNotFoundError,
        UnicodeDecodeError,
        ValueError,
        v7.v6.v5.v4.RefreshParseError,
    ):
        # Preserve the established parser/source failure handling.
        return v7.collect_one_v7(conn, state_dir, row)

    expected_time_range, baseline_assessed_at = (
        v7.v6.v5._expected_tavily_time_range(conn, row)
    )
    valid, reason, calls = _evaluate_v8_search_quality(
        session_path,
        expected_time_range,
    )
    search_call_count, distinct_search_query_count = (
        v7.v6.v5._search_metrics(calls)
    )

    if valid:
        # Only after the universal gate passes may downstream parsing/quarantine
        # persist candidate rows or the completed refresh result.
        result = v7.collect_one_v7(conn, state_dir, row)

        if result == "completed":
            with conn:
                v7.v6.v5.v4.update_search_quality_audit(
                    conn,
                    row["refresh_event_result_id"],
                    search_call_count=search_call_count,
                    distinct_search_query_count=distinct_search_query_count,
                    search_quality_valid=True,
                    search_quality_reason=reason,
                )
                v7.v6.v5.v4.base.recompute_batch(
                    conn,
                    row["refresh_id"],
                )

        return result

    # Fail closed before delegating to the downstream collector. At this point
    # no candidate rows for this result have been written.
    transcript_sha256 = hashlib.sha256(
        transcript.encode("utf-8")
    ).hexdigest()

    boundary_suffix = (
        f"; baseline_assessed_at={baseline_assessed_at}"
        if baseline_assessed_at
        else ""
    )
    gate_error = (
        f"search_quality_gate_v8_failed: {reason}{boundary_suffix}"
    )

    with conn:
        v7.v6.v5.v4.base.mark_failure(
            conn,
            row["refresh_event_result_id"],
            job_state="parse_failed",
            outcome="error",
            parse_error=gate_error,
            run=run,
        )
        conn.execute(
            """
            UPDATE refresh_event_results
            SET transcript_raw = ?,
                transcript_sha256 = ?,
                parser_version = ?
            WHERE refresh_event_result_id = ?
            """,
            (
                transcript,
                transcript_sha256,
                v7.v6.v5.v4.PARSER_VERSION,
                row["refresh_event_result_id"],
            ),
        )
        v7.v6.v5.v4.update_search_quality_audit(
            conn,
            row["refresh_event_result_id"],
            search_call_count=search_call_count,
            distinct_search_query_count=distinct_search_query_count,
            search_quality_valid=False,
            search_quality_reason=reason,
        )
        v7.v6.v5.v4.base.recompute_batch(
            conn,
            row["refresh_id"],
        )

    return "search_quality_failed"


def main(argv: list[str] | None = None) -> int:
    v7.v6.v5.v4.base.COLLECTOR_VERSION = COLLECTOR_VERSION
    v7.v6.v5.v4.base.collect_one = collect_one_v8
    return v7.v6.v5.v4.base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
