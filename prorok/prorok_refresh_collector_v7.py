#!/usr/bin/env python3
"""PROROK refresh collector v7 with deterministic query/retry hardening.

v7 extends the v6 NO_NEW_EVIDENCE gate:
- forbids any site: operator in Tavily search queries;
- if any of the first three core Tavily searches returns zero results, requires
  a later Tavily retry with a different query and the same required search
  parameters before NO_NEW_EVIDENCE can pass.

The downside-search semantic quality remains prompt-guided rather than enforced
with brittle keyword heuristics. v6 freshness verification remains unchanged.

Safety invariants:
- never writes official events, assessments, evidence_items, or sources;
- never invents search evidence;
- candidate quarantine rules remain owned by prorok_refresh_collector.py;
- positive candidate runs retain the existing v4/v5/v6 collection path.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

import prorok_refresh_collector_v6 as v6

COLLECTOR_VERSION = "7"
FORBIDDEN_SITE_TOKEN = "site:"
FORBIDDEN_AFTER_TOKEN = "after:"


def _result_count(payload: dict[str, Any] | None) -> int | None:
    if payload is None:
        return None

    count = payload.get("count")
    if isinstance(count, int) and not isinstance(count, bool):
        return count

    results = payload.get("results")
    if isinstance(results, list):
        return len(results)

    return None


def _retry_parameters_valid(
    call: dict[str, Any],
    expected_time_range: str | None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []

    tool_name = str(call.get("tool") or "")
    query = str(call.get("query") or "").strip()
    args = call.get("args") if isinstance(call.get("args"), dict) else {}

    if tool_name != v6.v5.REQUIRED_TOOL:
        errors.append(
            f"retry tool must be {v6.v5.REQUIRED_TOOL}; observed {tool_name or 'missing'}"
        )

    if not query:
        errors.append("retry query must be non-empty")

    lowered = query.lower()
    if FORBIDDEN_SITE_TOKEN in lowered:
        errors.append("retry query contains forbidden site: operator")
    if FORBIDDEN_AFTER_TOKEN in lowered:
        errors.append("retry query contains forbidden after: operator")

    if args.get("max_results") != v6.v5.REQUIRED_MAX_RESULTS:
        errors.append(
            f"retry max_results must be {v6.v5.REQUIRED_MAX_RESULTS}; "
            f"observed {args.get('max_results')!r}"
        )

    if args.get("topic") != v6.REQUIRED_TOPIC:
        errors.append(
            f"retry topic must be {v6.REQUIRED_TOPIC!r}; observed {args.get('topic')!r}"
        )

    actual_time_range = args.get("time_range")
    if expected_time_range is None:
        if actual_time_range not in (None, ""):
            errors.append(
                "retry time_range must be omitted; "
                f"observed {actual_time_range!r}"
            )
    elif actual_time_range != expected_time_range:
        errors.append(
            f"retry time_range must be {expected_time_range!r}; "
            f"observed {actual_time_range!r}"
        )

    return not errors, errors


def _evaluate_v7_search_quality(
    session_path: Path,
    expected_time_range: str | None,
) -> tuple[bool, str, list[dict[str, Any]]]:
    errors: list[str] = []

    v6_valid, v6_reason, calls = v6._evaluate_v6_search_quality(
        session_path,
        expected_time_range,
    )
    if not v6_valid:
        errors.append(v6_reason)

    # v6 checks the first three core queries. v7 closes the broader loophole by
    # rejecting site: in every Tavily search query, including retries.
    for index, call in enumerate(calls, start=1):
        if str(call.get("tool") or "") != v6.v5.REQUIRED_TOOL:
            continue
        query = str(call.get("query") or "")
        if FORBIDDEN_SITE_TOKEN in query.lower():
            errors.append(
                f"search #{index} query contains forbidden site: operator"
            )

    trace_calls, result_payloads, _verification_calls = v6._extract_trace(
        session_path
    )
    trace_core = trace_calls[: v6.v5.REQUIRED_CORE_SEARCH_CALLS]

    zero_core: list[tuple[int, dict[str, Any]]] = []
    if len(trace_core) >= v6.v5.REQUIRED_CORE_SEARCH_CALLS:
        for index, call in enumerate(trace_core, start=1):
            call_id = str(call.get("call_id") or "")
            payload = result_payloads.get(call_id)
            count = _result_count(payload)
            if count == 0:
                zero_core.append((index, call))

    used_retry_ids: set[str] = set()

    for core_index, core_call in zero_core:
        core_sequence = int(core_call.get("sequence") or 0)
        core_query = str(core_call.get("query") or "").strip()
        matched_retry: dict[str, Any] | None = None
        rejected_retry_reasons: list[str] = []

        for candidate in trace_calls[v6.v5.REQUIRED_CORE_SEARCH_CALLS :]:
            candidate_id = str(candidate.get("call_id") or "")
            if candidate_id and candidate_id in used_retry_ids:
                continue

            if int(candidate.get("sequence") or 0) <= core_sequence:
                continue

            candidate_query = str(candidate.get("query") or "").strip()
            if not candidate_query or candidate_query == core_query:
                continue

            valid_params, retry_errors = _retry_parameters_valid(
                candidate,
                expected_time_range,
            )
            if not valid_params:
                rejected_retry_reasons.extend(retry_errors)
                continue

            matched_retry = candidate
            if candidate_id:
                used_retry_ids.add(candidate_id)
            break

        if matched_retry is None:
            detail = ""
            if rejected_retry_reasons:
                unique = list(dict.fromkeys(rejected_retry_reasons))
                detail = "; observed later retry candidate issue(s): " + ", ".join(
                    unique[:4]
                )
            errors.append(
                f"search #{core_index} returned 0 results and lacks a later valid "
                f"tavily_search retry with a different query{detail}"
            )

    if errors:
        return (
            False,
            "v7 search protocol failed: " + "; ".join(errors),
            calls,
        )

    return (
        True,
        "ok v7: v6 Tavily/freshness rules pass, no Tavily query uses site:, "
        f"and zero-result core searches have valid later retries ({len(zero_core)} zero-result core search(es))",
        calls,
    )


def collect_one_v7(
    conn: sqlite3.Connection,
    state_dir: Path,
    row: sqlite3.Row,
) -> str:
    """Apply the v7 gate to NO_NEW_EVIDENCE; otherwise preserve the v6 path."""
    if not v6.v5.v4.search_quality_schema_available(conn):
        return v6.collect_one_v6(conn, state_dir, row)

    cron_id = row["cron_id"]
    if not cron_id:
        return v6.collect_one_v6(conn, state_dir, row)

    run = v6.v5.v4.base.find_latest_finished_run(state_dir, cron_id)
    if run is None or run.status not in {"ok", "success", "completed"}:
        return v6.collect_one_v6(conn, state_dir, row)

    if not run.session_id:
        return v6.collect_one_v6(conn, state_dir, row)

    try:
        session_path = v6.v5.v4.base.resolve_session_transcript_path(
            state_dir,
            run.session_id,
            run.session_key,
        )
        transcript = v6.v5.v4.base.extract_final_assistant_text(session_path)
        parsed = v6.v5.v4.parse_refresh_report(
            transcript,
            expected_event_id=row["event_id"],
            expected_baseline_probability=row["baseline_probability"],
        )
    except (
        FileNotFoundError,
        UnicodeDecodeError,
        ValueError,
        v6.v5.v4.RefreshParseError,
    ):
        return v6.collect_one_v6(conn, state_dir, row)

    if parsed.outcome != "no_new_evidence" or parsed.candidates:
        return v6.collect_one_v6(conn, state_dir, row)

    expected_time_range, baseline_assessed_at = (
        v6.v5._expected_tavily_time_range(conn, row)
    )
    valid, reason, calls = _evaluate_v7_search_quality(
        session_path,
        expected_time_range,
    )
    search_call_count, distinct_search_query_count = v6.v5._search_metrics(calls)

    if valid:
        result = v6.collect_one_v6(conn, state_dir, row)
        if result == "completed":
            with conn:
                v6.v5.v4.update_search_quality_audit(
                    conn,
                    row["refresh_event_result_id"],
                    search_call_count=search_call_count,
                    distinct_search_query_count=distinct_search_query_count,
                    search_quality_valid=True,
                    search_quality_reason=reason,
                )
                v6.v5.v4.base.recompute_batch(conn, row["refresh_id"])
        return result

    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    boundary_suffix = (
        f"; baseline_assessed_at={baseline_assessed_at}"
        if baseline_assessed_at
        else ""
    )
    gate_error = f"search_quality_gate_v7_failed: {reason}{boundary_suffix}"

    with conn:
        v6.v5.v4.base.mark_failure(
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
                v6.v5.v4.PARSER_VERSION,
                row["refresh_event_result_id"],
            ),
        )
        v6.v5.v4.update_search_quality_audit(
            conn,
            row["refresh_event_result_id"],
            search_call_count=search_call_count,
            distinct_search_query_count=distinct_search_query_count,
            search_quality_valid=False,
            search_quality_reason=reason,
        )
        v6.v5.v4.base.recompute_batch(conn, row["refresh_id"])

    return "search_quality_failed"


def main(argv: list[str] | None = None) -> int:
    v6.v5.v4.base.COLLECTOR_VERSION = COLLECTOR_VERSION
    v6.v5.v4.base.collect_one = collect_one_v7
    return v6.v5.v4.base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
