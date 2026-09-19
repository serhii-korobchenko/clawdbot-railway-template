#!/usr/bin/env python3
"""PROROK refresh collector v4 with deterministic search-quality auditing.

This module wraps the stable v3 collector. Before schema migration v7 is applied,
it deliberately delegates to the legacy collector so a code deploy is safe. Once
v7 audit columns exist, it records actual search tool usage from the OpenClaw
session JSONL and fail-closes NO_NEW_EVIDENCE results that were produced without
an adequate search attempt.

Safety invariants:
- never writes official events, assessments, evidence_items, or sources;
- never invents search evidence;
- candidate quarantine rules remain owned by prorok_refresh_collector.py;
- only NO_NEW_EVIDENCE is blocked by the search-quality gate in v4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import prorok_refresh_collector as base
from prorok_refresh_parser import PARSER_VERSION, RefreshParseError, parse_refresh_report

COLLECTOR_VERSION = "4"
SEARCH_TOOL_NAMES = {"web_search", "tavily_search"}
MIN_SEARCH_CALLS = 3
MIN_DISTINCT_SEARCH_QUERIES = 3
REQUIRED_V7_COLUMNS = {
    "search_call_count",
    "distinct_search_query_count",
    "search_quality_valid",
    "search_quality_reason",
}

LEGACY_COLLECT_ONE = base.collect_one


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def search_quality_schema_available(conn: sqlite3.Connection) -> bool:
    return REQUIRED_V7_COLUMNS.issubset(table_columns(conn, "refresh_event_results"))


def _tool_arguments(part: dict[str, Any]) -> dict[str, Any]:
    arguments = part.get("arguments")
    if isinstance(arguments, dict):
        return arguments

    partial = part.get("partialJson")
    if isinstance(partial, str) and partial.strip():
        try:
            decoded = json.loads(partial)
        except json.JSONDecodeError:
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _normalize_query(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None


def extract_search_metrics(session_path: Path) -> tuple[int, int, list[str]]:
    """Count real assistant search calls and distinct search queries in a session."""
    call_count = 0
    queries: list[str] = []
    distinct: set[str] = set()

    for item in base.read_jsonl(session_path):
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        if message.get("role") != "assistant":
            continue

        content = message.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "") not in {"toolCall", "tool_call"}:
                continue

            tool_name = str(part.get("name") or "")
            if tool_name not in SEARCH_TOOL_NAMES:
                continue

            call_count += 1
            query = _normalize_query(_tool_arguments(part).get("query"))
            if query is not None:
                queries.append(query)
                distinct.add(query)

    return call_count, len(distinct), queries


def evaluate_search_quality(
    search_call_count: int,
    distinct_search_query_count: int,
) -> tuple[bool, str]:
    valid = (
        search_call_count >= MIN_SEARCH_CALLS
        and distinct_search_query_count >= MIN_DISTINCT_SEARCH_QUERIES
    )
    if valid:
        return (
            True,
            f"ok: {search_call_count} search calls / "
            f"{distinct_search_query_count} distinct queries",
        )
    return (
        False,
        "insufficient search coverage: requires "
        f">={MIN_SEARCH_CALLS} search calls and "
        f">={MIN_DISTINCT_SEARCH_QUERIES} distinct queries; observed "
        f"{search_call_count} calls / {distinct_search_query_count} distinct queries",
    )


def update_search_quality_audit(
    conn: sqlite3.Connection,
    result_id: int,
    *,
    search_call_count: int,
    distinct_search_query_count: int,
    search_quality_valid: bool,
    search_quality_reason: str,
) -> None:
    conn.execute(
        """
        UPDATE refresh_event_results
        SET search_call_count = ?,
            distinct_search_query_count = ?,
            search_quality_valid = ?,
            search_quality_reason = ?
        WHERE refresh_event_result_id = ?
        """,
        (
            search_call_count,
            distinct_search_query_count,
            int(search_quality_valid),
            search_quality_reason,
            result_id,
        ),
    )


def collect_one_v4(
    conn: sqlite3.Connection,
    state_dir: Path,
    row: sqlite3.Row,
) -> str:
    """Collect one refresh result, adding the v7 search-quality gate when available."""
    if not search_quality_schema_available(conn):
        return LEGACY_COLLECT_ONE(conn, state_dir, row)

    result_id = row["refresh_event_result_id"]
    cron_id = row["cron_id"]

    if not cron_id:
        return "waiting:no_cron_id"

    run = base.find_latest_finished_run(state_dir, cron_id)
    if run is None:
        return "waiting:no_finished_run"

    if run.status not in {"ok", "success", "completed"}:
        with conn:
            base.mark_failure(
                conn,
                result_id,
                job_state="execution_failed",
                parse_error=f"cron finished with status={run.status!r}",
                run=run,
            )
            base.recompute_batch(conn, row["refresh_id"])
        return "execution_failed"

    source: tuple[str, str] | None = None
    source_missing_error: str | None = None
    search_call_count = 0
    distinct_search_query_count = 0

    try:
        if not run.session_id:
            raise FileNotFoundError("cron run has no sessionId")
        session_path = base.resolve_session_transcript_path(
            state_dir,
            run.session_id,
            run.session_key,
        )
        transcript = base.extract_final_assistant_text(session_path)
        transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        source = (transcript, transcript_sha256)
        (
            search_call_count,
            distinct_search_query_count,
            _queries,
        ) = extract_search_metrics(session_path)
    except UnicodeDecodeError as exc:
        with conn:
            base.mark_failure(
                conn,
                result_id,
                job_state="encoding_failed",
                parse_error=f"transcript UTF-8 decode failed: {exc}",
                run=run,
            )
            base.recompute_batch(conn, row["refresh_id"])
        return "encoding_failed"
    except (FileNotFoundError, ValueError) as exc:
        source_missing_error = str(exc)
        source = base.summary_fallback(run)

    if source is None:
        with conn:
            base.mark_failure(
                conn,
                result_id,
                job_state="source_missing",
                parse_error=(
                    source_missing_error
                    or "transcript unavailable and summary incomplete"
                ),
                run=run,
            )
            base.recompute_batch(conn, row["refresh_id"])
        return "source_missing"

    transcript, transcript_sha256 = source

    if (
        row["source_run_key"] == run.source_run_key
        and row["transcript_sha256"] == transcript_sha256
        and row["job_state"] == "completed"
    ):
        return "noop:already_collected"

    try:
        parsed = parse_refresh_report(
            transcript,
            expected_event_id=row["event_id"],
            expected_baseline_probability=row["baseline_probability"],
        )
        candidate_validation = base.validate_candidates(conn, row, parsed)
    except RefreshParseError as exc:
        with conn:
            base.mark_failure(
                conn,
                result_id,
                job_state="parse_failed",
                parse_error=str(exc),
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
                (transcript, transcript_sha256, PARSER_VERSION, result_id),
            )
            valid, reason = evaluate_search_quality(
                search_call_count,
                distinct_search_query_count,
            )
            update_search_quality_audit(
                conn,
                result_id,
                search_call_count=search_call_count,
                distinct_search_query_count=distinct_search_query_count,
                search_quality_valid=valid,
                search_quality_reason=reason,
            )
            base.recompute_batch(conn, row["refresh_id"])
        return "parse_failed"

    search_quality_valid, search_quality_reason = evaluate_search_quality(
        search_call_count,
        distinct_search_query_count,
    )

    # Fail closed only when the agent claims there is no new evidence. A positive
    # candidate result is still collected and quarantined, but its search-quality
    # metrics remain visible for audit and future tightening of recommendation rules.
    if parsed.outcome == "no_new_evidence" and not parsed.candidates and not search_quality_valid:
        gate_error = f"search_quality_gate_failed: {search_quality_reason}"
        with conn:
            base.mark_failure(
                conn,
                result_id,
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
                (transcript, transcript_sha256, PARSER_VERSION, result_id),
            )
            update_search_quality_audit(
                conn,
                result_id,
                search_call_count=search_call_count,
                distinct_search_query_count=distinct_search_query_count,
                search_quality_valid=False,
                search_quality_reason=search_quality_reason,
            )
            base.recompute_batch(conn, row["refresh_id"])
        return "search_quality_failed"

    try:
        with conn:
            base.apply_success(
                conn,
                row,
                run,
                transcript,
                transcript_sha256,
                parsed,
                candidate_validation,
            )
            update_search_quality_audit(
                conn,
                result_id,
                search_call_count=search_call_count,
                distinct_search_query_count=distinct_search_query_count,
                search_quality_valid=search_quality_valid,
                search_quality_reason=search_quality_reason,
            )
            base.recompute_batch(conn, row["refresh_id"])
    except sqlite3.IntegrityError as exc:
        with conn:
            base.mark_failure(
                conn,
                result_id,
                job_state="parse_failed",
                parse_error=f"collector integrity anomaly: {exc}",
                run=run,
            )
            update_search_quality_audit(
                conn,
                result_id,
                search_call_count=search_call_count,
                distinct_search_query_count=distinct_search_query_count,
                search_quality_valid=search_quality_valid,
                search_quality_reason=search_quality_reason,
            )
            base.recompute_batch(conn, row["refresh_id"])
        return "parse_failed"

    return "completed"


def main(argv: list[str] | None = None) -> int:
    # Reuse the stable collector process loop and lifecycle bookkeeping while
    # replacing only the per-result collection step.
    base.COLLECTOR_VERSION = COLLECTOR_VERSION
    base.collect_one = collect_one_v4
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
