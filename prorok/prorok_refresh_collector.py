#!/usr/bin/env python3
"""Deterministic collector for PROROK refresh dry-run results.

The collector correlates scheduled PROROK refresh_event_results with OpenClaw
cron run history and isolated-session transcripts, validates the final report
through prorok_refresh_parser, and writes only refresh audit/candidate tables.

Safety invariant: this module never writes events, assessments, evidence_items,
sources, or historical manual runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    from .prorok_refresh_parser import PARSER_VERSION, RefreshParseError, parse_refresh_report
except ImportError:  # direct script execution from /app/prorok
    from prorok_refresh_parser import PARSER_VERSION, RefreshParseError, parse_refresh_report

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
DEFAULT_STATE_DIR = "/data/.openclaw"
COLLECTOR_VERSION = "3"
TERMINAL_JOB_STATES = {
    "completed",
    "schedule_failed",
    "execution_failed",
    "timeout",
    "source_missing",
    "parse_failed",
    "encoding_failed",
}

BANNED_SOURCE_DOMAINS = {
    "facebook.com",
    "medium.com",
    "reddit.com",
    "substack.com",
    "tiktok.com",
    "twitter.com",
    "wikipedia.org",
    "x.com",
    "youtu.be",
    "youtube.com",
}


@dataclass(frozen=True)
class CronRun:
    cron_id: str
    run_at_ms: int
    status: str
    session_id: str | None
    session_key: str | None
    summary: str | None
    duration_ms: int | None
    model: str | None
    provider: str | None
    delivered: bool | None
    delivery_status: str | None

    @property
    def source_run_key(self) -> str | None:
        if not self.session_id:
            return None
        raw = f"{self.cron_id}|{self.run_at_ms}|{self.session_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def utc_now_sql() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).resolve()


def resolve_state_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(os.getenv("OPENCLAW_STATE_DIR") or DEFAULT_STATE_DIR).resolve()


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return ()
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
            if isinstance(item, dict):
                rows.append(item)
    return rows


def parse_cron_run(cron_id: str, item: dict[str, Any]) -> CronRun | None:
    if str(item.get("jobId") or "") != cron_id:
        return None
    if str(item.get("action") or "") != "finished":
        return None

    try:
        run_at_ms = int(item.get("runAtMs"))
    except (TypeError, ValueError):
        return None

    delivered_raw = item.get("delivered")
    delivered: bool | None
    if isinstance(delivered_raw, bool):
        delivered = delivered_raw
    else:
        delivered = None

    duration_ms = item.get("durationMs")
    try:
        duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        duration_ms = None

    return CronRun(
        cron_id=cron_id,
        run_at_ms=run_at_ms,
        status=str(item.get("status") or ""),
        session_id=str(item.get("sessionId")) if item.get("sessionId") else None,
        session_key=str(item.get("sessionKey")) if item.get("sessionKey") else None,
        summary=str(item.get("summary")) if item.get("summary") is not None else None,
        duration_ms=duration_ms,
        model=str(item.get("model")) if item.get("model") else None,
        provider=str(item.get("provider")) if item.get("provider") else None,
        delivered=delivered,
        delivery_status=(
            str(item.get("deliveryStatus")) if item.get("deliveryStatus") else None
        ),
    )


def find_latest_finished_run(state_dir: Path, cron_id: str) -> CronRun | None:
    path = state_dir / "cron" / "runs" / f"{cron_id}.jsonl"
    runs: list[CronRun] = []
    for item in read_jsonl(path):
        parsed = parse_cron_run(cron_id, item)
        if parsed is not None:
            runs.append(parsed)
    if not runs:
        return None
    return max(runs, key=lambda row: row.run_at_ms)


def extract_final_assistant_text(session_path: Path) -> str:
    """Return the final visible assistant text from an OpenClaw session JSONL."""
    last_text: str | None = None

    for item in read_jsonl(session_path):
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        role = message.get("role")
        content = message.get("content")

        if role != "assistant":
            continue

        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    part_type = str(part.get("type") or "")
                    if part_type in {"text", "output_text"} and isinstance(part.get("text"), str):
                        parts.append(part["text"])

        text = "".join(parts).strip()
        if text:
            last_text = text

    if last_text is None:
        raise ValueError("session transcript contains no assistant text")
    return last_text


def resolve_session_transcript_path(state_dir: Path, session_id: str) -> Path:
    """Resolve a live or OpenClaw-retired isolated-session transcript.

    OpenClaw may retire delete-after-run isolated sessions by renaming
    <sessionId>.jsonl to <sessionId>.jsonl.deleted.<ISO timestamp>. Prefer the
    live transcript when it still exists; otherwise use the newest retired
    transcript. The timestamp suffix is ISO-formatted, so filename ordering is
    chronological for files belonging to the same session id.
    """
    session_dir = state_dir / "agents" / "main" / "sessions"
    live_path = session_dir / f"{session_id}.jsonl"
    if live_path.exists():
        return live_path

    retired = sorted(session_dir.glob(f"{session_id}.jsonl.deleted.*"))
    if retired:
        return retired[-1]

    raise FileNotFoundError(
        f"session transcript missing: {live_path}; "
        f"no retired transcript matching {session_id}.jsonl.deleted.*"
    )


def transcript_for_run(state_dir: Path, run: CronRun) -> tuple[str, str]:
    if not run.session_id:
        raise FileNotFoundError("cron run has no sessionId")

    session_path = resolve_session_transcript_path(state_dir, run.session_id)
    text = extract_final_assistant_text(session_path)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


def summary_fallback(run: CronRun) -> tuple[str, str] | None:
    """Use summary only when it itself is a complete strict-parseable report."""
    summary = (run.summary or "").strip()
    if not summary.startswith("PROROK_REFRESH_DRY_RUN"):
        return None
    if "ASSESSMENT_RECOMMENDATION:" not in summary or "DB_ACTION:" not in summary:
        return None
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return summary, digest


def candidate_rows(parsed: Any) -> list[dict[str, Any]]:
    return [candidate.as_dict() for candidate in parsed.candidates]


def _parse_iso_datetime(value: str, field: str) -> tuple[datetime, bool]:
    """Parse an ISO date/datetime and return (UTC datetime, is_date_only)."""
    raw = value.strip()
    if not raw:
        raise RefreshParseError(f"{field} must not be empty")

    is_date_only = len(raw) == 10 and raw[4] == "-" and raw[7] == "-"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RefreshParseError(
            f"{field} must be an ISO-8601 date or datetime; got {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed, is_date_only


def resolve_baseline_datetime(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> datetime:
    baseline_assessment_id = row["baseline_assessment_id"]
    if baseline_assessment_id is None:
        raise RefreshParseError(
            "candidate validation requires baseline_assessment_id"
        )

    baseline = conn.execute(
        """
        SELECT assessed_at
        FROM assessments
        WHERE assessment_id = ?
          AND event_id = ?
        """,
        (baseline_assessment_id, row["event_id"]),
    ).fetchone()

    if baseline is None or not baseline["assessed_at"]:
        raise RefreshParseError(
            "candidate validation could not resolve baseline assessed_at"
        )

    baseline_dt, _ = _parse_iso_datetime(
        str(baseline["assessed_at"]),
        "baseline assessed_at",
    )
    return baseline_dt


def validate_candidates(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    parsed: Any,
) -> dict[int, dict[str, str | None]]:
    """Validate candidates independently and return deterministic quarantine state.

    Candidate-level metadata/source/date problems are audit outcomes, not
    collector execution failures. Global parser/baseline failures still raise
    RefreshParseError and fail the event result.
    """
    if not parsed.candidates:
        return {}

    baseline_dt = resolve_baseline_datetime(conn, row)
    validated: dict[int, dict[str, str | None]] = {}

    for candidate in parsed.candidates:
        state = "accepted"
        reason: str | None = None
        freshness: str | None = None

        try:
            published_dt, published_is_date_only = _parse_iso_datetime(
                candidate.published_at,
                f"CANDIDATE_EVIDENCE/{candidate.ordinal} published_at",
            )
            if published_is_date_only:
                is_after_baseline = published_dt.date() > baseline_dt.date()
            else:
                is_after_baseline = published_dt > baseline_dt
            freshness = (
                "new_after_last_assessment"
                if is_after_baseline
                else "missed_baseline_evidence"
            )
        except RefreshParseError as exc:
            state = "rejected_invalid_metadata"
            reason = str(exc)

        if state == "accepted":
            hostname = (urlparse(candidate.url).hostname or "").lower().rstrip(".")
            banned = next(
                (
                    domain
                    for domain in BANNED_SOURCE_DOMAINS
                    if hostname == domain or hostname.endswith(f".{domain}")
                ),
                None,
            )
            if banned is not None:
                state = "rejected_source_policy"
                reason = (
                    f"domain {hostname!r} is banned by the refresh evidence policy"
                )

        if state == "accepted":
            current_dt, _ = _parse_iso_datetime(
                candidate.published_at,
                f"CANDIDATE_EVIDENCE/{candidate.ordinal} published_at",
            )
            current_date = current_dt.date().isoformat()

            prior_rows = conn.execute(
                """
                SELECT DISTINCT c.published_at
                FROM refresh_candidate_evidence c
                JOIN refresh_event_results rer
                  ON rer.refresh_event_result_id = c.refresh_event_result_id
                WHERE rer.event_id = ?
                  AND c.url = ?
                  AND c.refresh_event_result_id != ?
                  AND c.published_at IS NOT NULL
                  AND TRIM(c.published_at) != ''
                ORDER BY c.published_at
                """,
                (
                    row["event_id"],
                    candidate.url,
                    row["refresh_event_result_id"],
                ),
            ).fetchall()

            if prior_rows:
                prior_raw = [str(prior["published_at"]) for prior in prior_rows]
                prior_dates: set[str] = set()
                invalid_prior: str | None = None
                for prior_value in prior_raw:
                    try:
                        prior_dt, _ = _parse_iso_datetime(
                            prior_value,
                            "historical candidate published_at",
                        )
                    except RefreshParseError as exc:
                        invalid_prior = str(exc)
                        break
                    prior_dates.add(prior_dt.date().isoformat())

                if invalid_prior is not None:
                    state = "rejected_invalid_metadata"
                    reason = invalid_prior
                elif prior_dates != {current_date}:
                    state = "rejected_date_conflict"
                    reason = (
                        f"URL {candidate.url!r} has current published_at "
                        f"{candidate.published_at!r}, while prior refresh candidate "
                        f"date(s) are {', '.join(sorted(prior_raw))}"
                    )

        validated[candidate.ordinal] = {
            "validation_state": state,
            "rejection_reason": reason,
            "freshness": freshness,
        }

    return validated


def mark_failure(
    conn: sqlite3.Connection,
    result_id: int,
    *,
    job_state: str,
    outcome: str = "error",
    parse_error: str | None = None,
    run: CronRun | None = None,
) -> None:
    fields = {
        "job_state": job_state,
        "outcome": outcome,
        "parse_error": parse_error,
        "collected_at": utc_now_sql(),
        "cron_run_at_ms": run.run_at_ms if run else None,
        "session_id": run.session_id if run else None,
        "session_key": run.session_key if run else None,
        "cron_summary_preview": run.summary if run else None,
        "cron_status": run.status if run else None,
        "duration_ms": run.duration_ms if run else None,
        "model": run.model if run else None,
        "provider": run.provider if run else None,
        "delivered": (
            int(run.delivered) if run and run.delivered is not None else None
        ),
        "delivery_status": run.delivery_status if run else None,
    }
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(
        f"UPDATE refresh_event_results SET {assignments} WHERE refresh_event_result_id = ?",
        (*fields.values(), result_id),
    )


def apply_success(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    run: CronRun,
    transcript: str,
    transcript_sha256: str,
    parsed: Any,
    candidate_validation: dict[int, dict[str, str | None]],
) -> None:
    source_run_key = run.source_run_key
    if source_run_key is None:
        raise RuntimeError("completed source run lacks source_run_key")

    existing = conn.execute(
        """
        SELECT refresh_event_result_id, transcript_sha256
        FROM refresh_event_results
        WHERE source_run_key = ?
        """,
        (source_run_key,),
    ).fetchone()

    if existing is not None and existing["refresh_event_result_id"] != row["refresh_event_result_id"]:
        if existing["transcript_sha256"] == transcript_sha256:
            raise RuntimeError(
                f"source_run_key already attached to result {existing['refresh_event_result_id']}"
            )
        raise RuntimeError("source_run_key collision/anomaly with different transcript hash")

    if (
        row["source_run_key"] == source_run_key
        and row["transcript_sha256"] == transcript_sha256
        and row["job_state"] == "completed"
    ):
        return

    if row["source_run_key"] == source_run_key and row["transcript_sha256"]:
        if row["transcript_sha256"] != transcript_sha256:
            raise RuntimeError("same source_run_key has a different transcript hash")

    fields = parsed.event_result_fields()
    accepted = [
        candidate
        for candidate in parsed.candidates
        if candidate_validation[candidate.ordinal]["validation_state"] == "accepted"
    ]
    rejected_count = len(parsed.candidates) - len(accepted)
    recommendation_valid = rejected_count == 0

    fields["outcome"] = (
        "new_evidence" if accepted
        else ("no_new_evidence" if parsed.candidates else parsed.outcome)
    )
    fields["new_evidence_count"] = len(accepted)
    fields["indicator_count"] = sum(
        1 for candidate in accepted if candidate.direction == "indicator"
    )
    fields["counterindicator_count"] = sum(
        1 for candidate in accepted if candidate.direction == "counterindicator"
    )
    fields["candidate_rejected_count"] = rejected_count
    fields["recommendation_valid"] = int(recommendation_valid)

    if not recommendation_valid:
        fields["recommended_probability"] = None
        fields["recommended_band"] = None
        fields["recommended_label"] = None
        fields["change_from_baseline"] = "no_update"
        fields["change_recommended"] = 0
        fields["recommendation_confidence"] = None
        fields["recommendation_reason"] = (
            f"LLM recommendation invalidated because {rejected_count} "
            "candidate(s) failed deterministic validation."
        )

    fields.update(
        {
            "job_state": "completed",
            "cron_run_at_ms": run.run_at_ms,
            "session_id": run.session_id,
            "session_key": run.session_key,
            "source_run_key": source_run_key,
            "cron_summary_preview": run.summary,
            "transcript_raw": transcript,
            "transcript_sha256": transcript_sha256,
            "parser_version": PARSER_VERSION,
            "parse_error": None,
            "collected_at": utc_now_sql(),
            "cron_status": run.status,
            "duration_ms": run.duration_ms,
            "model": run.model,
            "provider": run.provider,
            "delivered": int(run.delivered) if run.delivered is not None else None,
            "delivery_status": run.delivery_status,
        }
    )

    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(
        f"""
        UPDATE refresh_event_results
        SET {assignments}
        WHERE refresh_event_result_id = ?
        """,
        (*fields.values(), row["refresh_event_result_id"]),
    )

    conn.execute(
        "DELETE FROM refresh_candidate_evidence WHERE refresh_event_result_id = ?",
        (row["refresh_event_result_id"],),
    )

    for candidate in candidate_rows(parsed):
        conn.execute(
            """
            INSERT INTO refresh_candidate_evidence(
                refresh_event_result_id,
                ordinal,
                direction,
                strength,
                relevance,
                credibility,
                title,
                source,
                url,
                published_at,
                summary,
                why_it_matters,
                duplicate_risk,
                freshness,
                validation_state,
                rejection_reason
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["refresh_event_result_id"],
                candidate["ordinal"],
                candidate["direction"],
                candidate["strength"],
                candidate["relevance"],
                candidate["credibility"],
                candidate["title"],
                candidate["source"],
                candidate["url"],
                candidate["published_at"],
                candidate["summary"],
                candidate["why_it_matters"],
                candidate["duplicate_risk"],
                candidate_validation[candidate["ordinal"]]["freshness"],
                candidate_validation[candidate["ordinal"]]["validation_state"],
                candidate_validation[candidate["ordinal"]]["rejection_reason"],
            ),
        )


def recompute_batch(conn: sqlite3.Connection, refresh_id: int) -> None:
    rows = conn.execute(
        """
        SELECT job_state, outcome, new_evidence_count, change_recommended
        FROM refresh_event_results
        WHERE refresh_id = ?
        """,
        (refresh_id,),
    ).fetchall()

    if not rows:
        return

    terminal = all(row["job_state"] in TERMINAL_JOB_STATES for row in rows)
    valid = [row for row in rows if row["job_state"] == "completed"]
    failures = [row for row in rows if row["job_state"] != "completed" and row["job_state"] in TERMINAL_JOB_STATES]

    events_checked = len(valid)
    events_with_new_evidence = sum(1 for row in valid if row["outcome"] == "new_evidence")
    new_evidence_count = sum(int(row["new_evidence_count"] or 0) for row in valid)
    recommendations_count = sum(int(row["change_recommended"] or 0) for row in valid)
    no_change_count = sum(
        1
        for row in valid
        if row["outcome"] == "no_new_evidence" or int(row["change_recommended"] or 0) == 0
    )
    error_count = len(failures)

    if terminal:
        if valid and not failures:
            status = "completed"
        elif valid:
            status = "partial"
        else:
            status = "failed"
        phase = "done"
        finished_at = utc_now_sql()
    else:
        status = "running"
        phase = "collecting"
        finished_at = None

    conn.execute(
        """
        UPDATE refresh_runs
        SET phase = ?,
            status = ?,
            finished_at = ?,
            events_checked = ?,
            events_with_new_evidence = ?,
            new_evidence_count = ?,
            recommendations_count = ?,
            no_change_count = ?,
            error_count = ?,
            last_collected_at = ?,
            collector_version = ?
        WHERE refresh_id = ?
        """,
        (
            phase,
            status,
            finished_at,
            events_checked,
            events_with_new_evidence,
            new_evidence_count,
            recommendations_count,
            no_change_count,
            error_count,
            utc_now_sql(),
            COLLECTOR_VERSION,
            refresh_id,
        ),
    )


def collect_one(
    conn: sqlite3.Connection,
    state_dir: Path,
    row: sqlite3.Row,
) -> str:
    result_id = row["refresh_event_result_id"]
    cron_id = row["cron_id"]

    if not cron_id:
        return "waiting:no_cron_id"

    run = find_latest_finished_run(state_dir, cron_id)
    if run is None:
        return "waiting:no_finished_run"

    if run.status not in {"ok", "success", "completed"}:
        with conn:
            mark_failure(
                conn,
                result_id,
                job_state="execution_failed",
                parse_error=f"cron finished with status={run.status!r}",
                run=run,
            )
            recompute_batch(conn, row["refresh_id"])
        return "execution_failed"

    source: tuple[str, str] | None = None
    source_missing_error: str | None = None

    try:
        source = transcript_for_run(state_dir, run)
    except UnicodeDecodeError as exc:
        with conn:
            mark_failure(
                conn,
                result_id,
                job_state="encoding_failed",
                parse_error=f"transcript UTF-8 decode failed: {exc}",
                run=run,
            )
            recompute_batch(conn, row["refresh_id"])
        return "encoding_failed"
    except (FileNotFoundError, ValueError) as exc:
        source_missing_error = str(exc)
        source = summary_fallback(run)

    if source is None:
        with conn:
            mark_failure(
                conn,
                result_id,
                job_state="source_missing",
                parse_error=source_missing_error or "transcript unavailable and summary incomplete",
                run=run,
            )
            recompute_batch(conn, row["refresh_id"])
        return "source_missing"

    transcript, transcript_sha256 = source

    if row["source_run_key"] == run.source_run_key and row["transcript_sha256"] == transcript_sha256 and row["job_state"] == "completed":
        return "noop:already_collected"

    try:
        parsed = parse_refresh_report(
            transcript,
            expected_event_id=row["event_id"],
            expected_baseline_probability=row["baseline_probability"],
        )
        candidate_validation = validate_candidates(conn, row, parsed)
    except RefreshParseError as exc:
        with conn:
            mark_failure(
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
            recompute_batch(conn, row["refresh_id"])
        return "parse_failed"

    try:
        with conn:
            apply_success(
                conn,
                row,
                run,
                transcript,
                transcript_sha256,
                parsed,
                candidate_validation,
            )
            recompute_batch(conn, row["refresh_id"])
    except sqlite3.IntegrityError as exc:
        with conn:
            mark_failure(
                conn,
                result_id,
                job_state="parse_failed",
                parse_error=f"collector integrity anomaly: {exc}",
                run=run,
            )
            recompute_batch(conn, row["refresh_id"])
        return "parse_failed"

    return "completed"


def pending_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM refresh_event_results
        WHERE job_state IN ('pending', 'scheduled')
        ORDER BY refresh_id, refresh_event_result_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def collect_once(db: Path, state_dir: Path, limit: int = 100) -> dict[str, int]:
    counts: dict[str, int] = {}
    conn = connect_db(db)
    try:
        rows = pending_rows(conn, limit)
        if rows:
            refresh_ids = sorted({int(row["refresh_id"]) for row in rows})
            with conn:
                for refresh_id in refresh_ids:
                    conn.execute(
                        """
                        UPDATE refresh_runs
                        SET phase = 'collecting',
                            collection_started_at = COALESCE(collection_started_at, ?),
                            collector_version = ?
                        WHERE refresh_id = ?
                          AND status = 'running'
                        """,
                        (utc_now_sql(), COLLECTOR_VERSION, refresh_id),
                    )

        for row in rows:
            result = collect_one(conn, state_dir, row)
            counts[result] = counts.get(result, 0) + 1
        return counts
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect PROROK refresh cron/session results")
    parser.add_argument("--db", help="PROROK SQLite DB path")
    parser.add_argument("--state-dir", help="OpenClaw state dir")
    parser.add_argument("--once", action="store_true", help="Run one collection pass and exit")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)

    db = resolve_db(args.db)
    state_dir = resolve_state_dir(args.state_dir)

    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1
    if args.limit < 1:
        print("ERROR: --limit must be >= 1", file=sys.stderr)
        return 2

    stopping = False

    def stop_handler(_signal: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    while True:
        try:
            counts = collect_once(db, state_dir, args.limit)
            if counts:
                print(json.dumps({"collector": COLLECTOR_VERSION, "counts": counts}, ensure_ascii=False))
        except Exception as exc:
            print(f"[prorok-refresh-collector] pass failed: {exc}", file=sys.stderr)
            if args.once:
                return 1

        if args.once or stopping:
            return 0

        deadline = time.monotonic() + max(args.interval_seconds, 1.0)
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(0.5, max(deadline - time.monotonic(), 0.0)))


if __name__ == "__main__":
    raise SystemExit(main())
