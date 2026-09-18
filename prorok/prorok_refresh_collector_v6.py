#!/usr/bin/env python3
"""PROROK refresh collector v6 with deterministic Tavily freshness-verification gating.

v6 extends the v5 search-quality gate for NO_NEW_EVIDENCE results:
- first three searches must still satisfy the Tavily v5 protocol;
- first three searches must use topic="news";
- search #3 must use a natural-language downside query rather than the internal
  "counterindicator" label;
- for each of the first three search result sets, the first three results that
  have a URL but no published timestamp must be covered by a later
  tavily_extract or web_fetch verification call before NO_NEW_EVIDENCE can pass.

The verification gate validates the attempt, not the truth of the extracted date.
Candidate quarantine and official-state safety remain unchanged.

Safety invariants:
- never writes official events, assessments, evidence_items, or sources;
- never invents search evidence;
- candidate quarantine rules remain owned by prorok_refresh_collector.py;
- positive candidate runs retain the v4/v5 collection path.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import prorok_refresh_collector_v5 as v5

COLLECTOR_VERSION = "6"
REQUIRED_TOPIC = "news"
VERIFICATION_TOOLS = {"tavily_extract", "web_fetch"}
VERIFY_UNDATED_PER_SEARCH = 3
_INTERNAL_COUNTERINDICATOR_RE = re.compile(
    r"\bcounter[\s-]?indicators?\b",
    re.IGNORECASE,
)


def _normalize_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized.rstrip("/")


def _decode_tool_result_payload(content: Any) -> dict[str, Any] | None:
    texts: list[str] = []

    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)

    for text in texts:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    return None


def _extract_trace(session_path: Path) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    """Return ordered search calls, tool-result payloads by call id, and verification calls."""
    search_calls: list[dict[str, Any]] = []
    result_payloads: dict[str, dict[str, Any]] = {}
    verification_calls: list[dict[str, Any]] = []

    for sequence, item in enumerate(v5.v4.base.read_jsonl(session_path), start=1):
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        role = str(message.get("role") or "")
        content = message.get("content")

        if role == "assistant" and isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if str(part.get("type") or "") not in {"toolCall", "tool_call"}:
                    continue

                tool_name = str(part.get("name") or "")
                args = v5.v4._tool_arguments(part)
                call_id = str(part.get("id") or part.get("toolCallId") or "")

                if tool_name in v5.v4.SEARCH_TOOL_NAMES:
                    search_calls.append(
                        {
                            "sequence": sequence,
                            "tool": tool_name,
                            "query": v5.v4._normalize_query(args.get("query")) or "",
                            "args": args,
                            "call_id": call_id,
                        }
                    )

                if tool_name in VERIFICATION_TOOLS:
                    verification_calls.append(
                        {
                            "sequence": sequence,
                            "tool": tool_name,
                            "args": args,
                            "call_id": call_id,
                        }
                    )

        if role == "toolResult":
            tool_call_id = str(message.get("toolCallId") or message.get("tool_call_id") or "")
            if not tool_call_id:
                continue
            payload = _decode_tool_result_payload(content)
            if payload is not None:
                result_payloads[tool_call_id] = payload

    return search_calls, result_payloads, verification_calls


def _verification_urls(args: dict[str, Any]) -> set[str]:
    urls: set[str] = set()

    one = _normalize_url(args.get("url"))
    if one:
        urls.add(one)

    many = args.get("urls")
    if isinstance(many, list):
        for value in many:
            normalized = _normalize_url(value)
            if normalized:
                urls.add(normalized)
    elif isinstance(many, str):
        normalized = _normalize_url(many)
        if normalized:
            urls.add(normalized)

    return urls


def _undated_verification_targets(
    core_calls: list[dict[str, Any]],
    result_payloads: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], list[str]]:
    targets: dict[str, int] = {}
    errors: list[str] = []

    for index, call in enumerate(core_calls, start=1):
        call_id = str(call.get("call_id") or "")
        if not call_id:
            errors.append(f"search #{index} is missing tool call id")
            continue

        payload = result_payloads.get(call_id)
        if payload is None:
            errors.append(f"search #{index} tool result payload is missing")
            continue

        results = payload.get("results")
        if not isinstance(results, list):
            errors.append(f"search #{index} tool result has no results array")
            continue

        selected = 0
        for result in results:
            if selected >= VERIFY_UNDATED_PER_SEARCH:
                break
            if not isinstance(result, dict):
                continue

            url = _normalize_url(result.get("url"))
            if not url:
                continue

            published = result.get("published")
            if isinstance(published, str) and published.strip():
                continue

            targets[url] = min(
                targets.get(url, int(call.get("sequence") or 0)),
                int(call.get("sequence") or 0),
            )
            selected += 1

    return targets, errors


def _evaluate_v6_search_quality(
    session_path: Path,
    expected_time_range: str | None,
) -> tuple[bool, str, list[dict[str, Any]]]:
    errors: list[str] = []

    calls = v5._extract_search_calls(session_path)
    v5_valid, v5_reason = v5._evaluate_v5_search_quality(
        calls,
        expected_time_range,
    )
    if not v5_valid:
        errors.append(v5_reason)

    core = calls[: v5.REQUIRED_CORE_SEARCH_CALLS]

    for index, call in enumerate(core, start=1):
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        topic = args.get("topic")
        if topic != REQUIRED_TOPIC:
            errors.append(
                f"search #{index} topic must be {REQUIRED_TOPIC!r}; observed {topic!r}"
            )

    if len(core) >= 3:
        downside_query = str(core[2].get("query") or "")
        if _INTERNAL_COUNTERINDICATOR_RE.search(downside_query):
            errors.append(
                "search #3 query must use natural-language downside wording; "
                "internal counterindicator terminology is forbidden"
            )

    trace_calls, result_payloads, verification_calls = _extract_trace(session_path)
    trace_core = trace_calls[: v5.REQUIRED_CORE_SEARCH_CALLS]

    if len(trace_core) < v5.REQUIRED_CORE_SEARCH_CALLS:
        errors.append(
            "unable to correlate first 3 search calls with transcript tool results"
        )
        verification_targets: dict[str, int] = {}
    else:
        verification_targets, target_errors = _undated_verification_targets(
            trace_core,
            result_payloads,
        )
        errors.extend(target_errors)

    verified_after: dict[str, int] = {}

    for call in verification_calls:
        call_sequence = int(call.get("sequence") or 0)
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        for url in _verification_urls(args):
            verified_after[url] = max(verified_after.get(url, 0), call_sequence)

    missing_verification = sorted(
        url
        for url, required_after_sequence in verification_targets.items()
        if verified_after.get(url, 0) <= required_after_sequence
    )
    if missing_verification:
        preview = ", ".join(missing_verification[:3])
        if len(missing_verification) > 3:
            preview += f", ... (+{len(missing_verification) - 3} more)"
        errors.append(
            "freshness verification missing for "
            f"{len(missing_verification)} required undated search URL(s): {preview}"
        )

    if errors:
        return (
            False,
            "v6 search protocol failed: " + "; ".join(errors),
            calls,
        )

    return (
        True,
        "ok v6: first 3 searches satisfy v5 Tavily rules, use topic=news, "
        "search #3 uses natural downside wording, and required undated top-result "
        f"freshness verification is covered ({len(verification_targets)} URL(s))",
        calls,
    )


def collect_one_v6(
    conn: sqlite3.Connection,
    state_dir: Path,
    row: sqlite3.Row,
) -> str:
    """Apply the v6 gate to NO_NEW_EVIDENCE; otherwise preserve the v5 path."""
    if not v5.v4.search_quality_schema_available(conn):
        return v5.collect_one_v5(conn, state_dir, row)

    cron_id = row["cron_id"]
    if not cron_id:
        return v5.collect_one_v5(conn, state_dir, row)

    run = v5.v4.base.find_latest_finished_run(state_dir, cron_id)
    if run is None or run.status not in {"ok", "success", "completed"}:
        return v5.collect_one_v5(conn, state_dir, row)

    if not run.session_id:
        return v5.collect_one_v5(conn, state_dir, row)

    try:
        session_path = v5.v4.base.resolve_session_transcript_path(
            state_dir,
            run.session_id,
            run.session_key,
        )
        transcript = v5.v4.base.extract_final_assistant_text(session_path)
        parsed = v5.v4.parse_refresh_report(
            transcript,
            expected_event_id=row["event_id"],
            expected_baseline_probability=row["baseline_probability"],
        )
    except (FileNotFoundError, UnicodeDecodeError, ValueError, v5.v4.RefreshParseError):
        return v5.collect_one_v5(conn, state_dir, row)

    if parsed.outcome != "no_new_evidence" or parsed.candidates:
        return v5.collect_one_v5(conn, state_dir, row)

    expected_time_range, search_boundary_at = v5._expected_tavily_time_range(
        conn,
        row,
    )
    valid, reason, calls = _evaluate_v6_search_quality(
        session_path,
        expected_time_range,
    )
    search_call_count, distinct_search_query_count = v5._search_metrics(calls)

    if valid:
        result = v5.collect_one_v5(conn, state_dir, row)
        if result == "completed":
            with conn:
                v5.v4.update_search_quality_audit(
                    conn,
                    row["refresh_event_result_id"],
                    search_call_count=search_call_count,
                    distinct_search_query_count=distinct_search_query_count,
                    search_quality_valid=True,
                    search_quality_reason=reason,
                )
                v5.v4.base.recompute_batch(conn, row["refresh_id"])
        return result

    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    boundary_suffix = (
        f"; search_boundary_at={search_boundary_at}"
        if search_boundary_at
        else ""
    )
    gate_error = f"search_quality_gate_v6_failed: {reason}{boundary_suffix}"

    with conn:
        v5.v4.base.mark_failure(
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
                v5.v4.PARSER_VERSION,
                row["refresh_event_result_id"],
            ),
        )
        v5.v4.update_search_quality_audit(
            conn,
            row["refresh_event_result_id"],
            search_call_count=search_call_count,
            distinct_search_query_count=distinct_search_query_count,
            search_quality_valid=False,
            search_quality_reason=reason,
        )
        v5.v4.base.recompute_batch(conn, row["refresh_id"])

    return "search_quality_failed"


def main(argv: list[str] | None = None) -> int:
    v5.v4.base.COLLECTOR_VERSION = COLLECTOR_VERSION
    v5.v4.base.collect_one = collect_one_v6
    return v5.v4.base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
