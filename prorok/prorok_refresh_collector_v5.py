#!/usr/bin/env python3
"""PROROK refresh collector v5 with deterministic Tavily search-quality gating.

This module builds on collector v4. For NO_NEW_EVIDENCE results it validates the
actual ordered search tool calls from the OpenClaw session transcript before the
result can be accepted as completed.

Safety invariants:
- never writes official events, assessments, evidence_items, or sources;
- never invents search evidence;
- candidate quarantine rules remain owned by prorok_refresh_collector.py;
- positive candidate runs retain the v4 collection/quarantine path;
- NO_NEW_EVIDENCE fails closed unless its first three search calls satisfy the
  deterministic Tavily protocol.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import prorok_refresh_collector_v4 as v4

COLLECTOR_VERSION = "5"
REQUIRED_CORE_SEARCH_CALLS = 3
REQUIRED_TOOL = "tavily_search"
REQUIRED_MAX_RESULTS = 7
FORBIDDEN_QUERY_TOKENS = ("after:", "site:news")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _expected_tavily_time_range(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> tuple[str | None, str | None]:
    """Return the deterministic coarse Tavily range and baseline timestamp."""
    assessment_id = row["baseline_assessment_id"]
    if assessment_id is None:
        return None, None

    assessment = conn.execute(
        """
        SELECT assessed_at
        FROM assessments
        WHERE assessment_id = ?
          AND event_id = ?
        """,
        (assessment_id, row["event_id"]),
    ).fetchone()
    if assessment is None:
        return None, None

    assessed_at_raw = assessment["assessed_at"]
    assessed_at = _parse_datetime(assessed_at_raw)
    if assessed_at is None:
        return None, str(assessed_at_raw or "")

    reference = _parse_datetime(row["expected_run_at"]) or datetime.now(timezone.utc)
    if assessed_at > reference:
        return None, str(assessed_at_raw or "")

    age_days = (reference.date() - assessed_at.date()).days

    if age_days <= 1:
        return "day", str(assessed_at_raw or "")
    if age_days <= 7:
        return "week", str(assessed_at_raw or "")
    if age_days <= 31:
        return "month", str(assessed_at_raw or "")
    if age_days <= 365:
        return "year", str(assessed_at_raw or "")
    return None, str(assessed_at_raw or "")


def _extract_search_calls(session_path: Path) -> list[dict[str, Any]]:
    """Return ordered web/tavily search calls from assistant messages."""
    calls: list[dict[str, Any]] = []

    for item in v4.base.read_jsonl(session_path):
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
            if tool_name not in v4.SEARCH_TOOL_NAMES:
                continue

            args = v4._tool_arguments(part)
            query = v4._normalize_query(args.get("query"))

            calls.append(
                {
                    "tool": tool_name,
                    "query": query or "",
                    "args": args,
                }
            )

    return calls


def _search_metrics(calls: list[dict[str, Any]]) -> tuple[int, int]:
    queries = {
        str(call.get("query") or "").strip()
        for call in calls
        if str(call.get("query") or "").strip()
    }
    return len(calls), len(queries)


def _evaluate_v5_search_quality(
    calls: list[dict[str, Any]],
    expected_time_range: str | None,
) -> tuple[bool, str]:
    errors: list[str] = []
    call_count, distinct_count = _search_metrics(calls)

    if call_count < REQUIRED_CORE_SEARCH_CALLS:
        errors.append(
            f"requires >={REQUIRED_CORE_SEARCH_CALLS} search calls; observed {call_count}"
        )

    core = calls[:REQUIRED_CORE_SEARCH_CALLS]
    core_queries = [
        str(call.get("query") or "").strip()
        for call in core
        if str(call.get("query") or "").strip()
    ]
    if len(set(core_queries)) < REQUIRED_CORE_SEARCH_CALLS:
        errors.append(
            "first 3 search calls must contain 3 distinct non-empty queries"
        )

    for index, call in enumerate(core, start=1):
        tool_name = str(call.get("tool") or "")
        query = str(call.get("query") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}

        if tool_name != REQUIRED_TOOL:
            errors.append(
                f"search #{index} tool must be {REQUIRED_TOOL}; observed {tool_name or 'missing'}"
            )

        max_results = args.get("max_results")
        if max_results != REQUIRED_MAX_RESULTS:
            errors.append(
                f"search #{index} max_results must be {REQUIRED_MAX_RESULTS}; "
                f"observed {max_results!r}"
            )

        actual_time_range = args.get("time_range")
        if expected_time_range is None:
            if actual_time_range not in (None, ""):
                errors.append(
                    f"search #{index} time_range must be omitted; "
                    f"observed {actual_time_range!r}"
                )
        elif actual_time_range != expected_time_range:
            errors.append(
                f"search #{index} time_range must be {expected_time_range!r}; "
                f"observed {actual_time_range!r}"
            )

        lowered = query.lower()
        for forbidden in FORBIDDEN_QUERY_TOKENS:
            if forbidden in lowered:
                errors.append(
                    f"search #{index} query contains forbidden token {forbidden!r}"
                )

    if errors:
        return (
            False,
            "v5 search protocol failed: " + "; ".join(errors),
        )

    expected_label = expected_time_range if expected_time_range is not None else "omitted"
    return (
        True,
        "ok v5: first 3 searches use tavily_search with "
        f"max_results=7, time_range={expected_label}, 3 distinct queries, "
        "and no forbidden query operators",
    )


def collect_one_v5(
    conn: sqlite3.Connection,
    state_dir: Path,
    row: sqlite3.Row,
) -> str:
    """Apply the deterministic v5 gate to NO_NEW_EVIDENCE, otherwise use v4."""
    if not v4.search_quality_schema_available(conn):
        return v4.collect_one_v4(conn, state_dir, row)

    cron_id = row["cron_id"]
    if not cron_id:
        return v4.collect_one_v4(conn, state_dir, row)

    run = v4.base.find_latest_finished_run(state_dir, cron_id)
    if run is None or run.status not in {"ok", "success", "completed"}:
        return v4.collect_one_v4(conn, state_dir, row)

    if not run.session_id:
        return v4.collect_one_v4(conn, state_dir, row)

    try:
        session_path = v4.base.resolve_session_transcript_path(
            state_dir,
            run.session_id,
            run.session_key,
        )
        transcript = v4.base.extract_final_assistant_text(session_path)
        parsed = v4.parse_refresh_report(
            transcript,
            expected_event_id=row["event_id"],
            expected_baseline_probability=row["baseline_probability"],
        )
        calls = _extract_search_calls(session_path)
    except (FileNotFoundError, UnicodeDecodeError, ValueError, v4.RefreshParseError):
        return v4.collect_one_v4(conn, state_dir, row)

    if parsed.outcome != "no_new_evidence" or parsed.candidates:
        return v4.collect_one_v4(conn, state_dir, row)

    expected_time_range, baseline_assessed_at = _expected_tavily_time_range(conn, row)
    valid, reason = _evaluate_v5_search_quality(calls, expected_time_range)
    search_call_count, distinct_search_query_count = _search_metrics(calls)

    if valid:
        result = v4.collect_one_v4(conn, state_dir, row)
        if result == "completed":
            with conn:
                v4.update_search_quality_audit(
                    conn,
                    row["refresh_event_result_id"],
                    search_call_count=search_call_count,
                    distinct_search_query_count=distinct_search_query_count,
                    search_quality_valid=True,
                    search_quality_reason=reason,
                )
                v4.base.recompute_batch(conn, row["refresh_id"])
        return result

    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    boundary_suffix = (
        f"; baseline_assessed_at={baseline_assessed_at}"
        if baseline_assessed_at
        else ""
    )
    gate_error = f"search_quality_gate_v5_failed: {reason}{boundary_suffix}"

    with conn:
        v4.base.mark_failure(
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
                v4.PARSER_VERSION,
                row["refresh_event_result_id"],
            ),
        )
        v4.update_search_quality_audit(
            conn,
            row["refresh_event_result_id"],
            search_call_count=search_call_count,
            distinct_search_query_count=distinct_search_query_count,
            search_quality_valid=False,
            search_quality_reason=reason,
        )
        v4.base.recompute_batch(conn, row["refresh_id"])

    return "search_quality_failed"


def main(argv: list[str] | None = None) -> int:
    v4.base.COLLECTOR_VERSION = COLLECTOR_VERSION
    v4.base.collect_one = collect_one_v5
    return v4.base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
