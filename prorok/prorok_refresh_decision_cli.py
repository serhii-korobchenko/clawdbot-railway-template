#!/usr/bin/env python3
"""Apply an explicit user decision to a PROROK refresh recommendation.

This CLI is the deterministic write boundary for Telegram decision actions.
It never asks an LLM to change an official forecast.

Rules:
- schema v8 or newer is required;
- only completed, valid refresh recommendations may be decided;
- the refresh baseline must still be the current official assessment;
- one final decision is allowed per refresh_event_result_id;
- an undecided refresh is rejected if a newer refresh for the same event has
  already been finalized;
- every final decision promotes all quarantine-accepted refresh candidates into
  official evidence;
- accept/custom append an official assessment;
- keep_current promotes accepted evidence without creating an assessment;
- evidence promotion + assessment + decision + run metadata commit atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    from .prorok_evidence_cli import canonicalize_url
except ImportError:  # direct script execution from /app/prorok
    from prorok_evidence_cli import canonicalize_url

DEFAULT_PROROK_HOME = "/data/workspace/prorok"
DEFAULT_DB_NAME = "prorok.sqlite3"
MIN_SCHEMA_VERSION = 8

DECISION_ACCEPT = "accept_recommendation"
DECISION_CUSTOM = "custom_probability"
DECISION_KEEP = "keep_current"
DECISION_TYPES = (DECISION_ACCEPT, DECISION_CUSTOM, DECISION_KEEP)

SOURCE_CHOICES = ("telegram", "manual_cli", "system")

PROBABILITY_SCALE = (
    (0, 5, "0-5%", "Віддалена можливість"),
    (10, 20, "10-20%", "Низька ймовірність"),
    (25, 35, "25-35%", "Малоймовірно"),
    (40, 50, "40-50%", "Реалістична можливість"),
    (55, 75, "55-75%", "Ймовірно"),
    (80, 90, "80-90%", "Висока ймовірність"),
    (95, 100, "95-100%", "Майже напевно"),
)


class CliError(RuntimeError):
    """User-facing deterministic decision error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_home(explicit_home: str | None) -> Path:
    if explicit_home:
        return Path(explicit_home).expanduser().resolve()
    if os.getenv("PROROK_HOME"):
        return Path(os.environ["PROROK_HOME"]).expanduser().resolve()
    if os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        return Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]).expanduser().resolve() / "prorok"
    if os.getenv("DATA_DIR"):
        return Path(os.environ["DATA_DIR"]).expanduser().resolve() / "workspace" / "prorok"
    return Path(DEFAULT_PROROK_HOME).resolve()


def resolve_db(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db).expanduser().resolve()
    if os.getenv("PROROK_DB"):
        return Path(os.environ["PROROK_DB"]).expanduser().resolve()
    return resolve_home(args.home) / DEFAULT_DB_NAME


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise CliError(f"PROROK DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[object] = (),
) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = fetch_one(conn, "SELECT value FROM meta WHERE key = 'schema_version'")
    return None if row is None else str(row["value"])


def require_schema_v8(conn: sqlite3.Connection) -> None:
    version = schema_version(conn)
    try:
        version_number = int(version) if version is not None else None
    except (TypeError, ValueError) as exc:
        raise CliError(
            f"schema v{MIN_SCHEMA_VERSION}+ required; current schema_version={version!r}"
        ) from exc
    if version_number is None or version_number < MIN_SCHEMA_VERSION:
        raise CliError(
            f"schema v{MIN_SCHEMA_VERSION}+ required; current schema_version={version!r}"
        )

    required_tables = (
        "refresh_user_decisions",
        "refresh_candidate_evidence",
        "refresh_candidate_promotions",
    )
    for table in required_tables:
        row = fetch_one(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if row is None:
            raise CliError(f"schema v8 table {table} is missing")

    decision_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(refresh_user_decisions)")
    }
    if "run_id" not in decision_columns:
        raise CliError("schema v8 column refresh_user_decisions.run_id is missing")


def map_probability(probability: int) -> tuple[str, str]:
    for low, high, band, label in PROBABILITY_SCALE:
        if low <= probability <= high:
            return band, label
    raise CliError(
        f"probability {probability}% is outside the defined PROROK probability scale bands"
    )


def current_assessment(conn: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        """
        SELECT
            assessment_id,
            probability_percent,
            probability_band,
            probability_label,
            confidence
        FROM assessments
        WHERE event_id = ?
        ORDER BY assessed_at DESC, assessment_id DESC
        LIMIT 1
        """,
        (event_id,),
    )


def load_refresh_result(
    conn: sqlite3.Connection,
    refresh_event_result_id: int,
) -> sqlite3.Row:
    row = fetch_one(
        conn,
        """
        SELECT
            refresh_event_result_id,
            refresh_id,
            event_id,
            event_title_snapshot,
            baseline_assessment_id,
            baseline_probability,
            job_state,
            outcome,
            recommended_probability,
            recommendation_confidence,
            recommendation_reason,
            recommended_band,
            recommended_label,
            change_recommended,
            recommendation_valid,
            candidate_rejected_count
        FROM refresh_event_results
        WHERE refresh_event_result_id = ?
        """,
        (refresh_event_result_id,),
    )
    if row is None:
        raise CliError(
            f"refresh_event_result_id not found: {refresh_event_result_id}"
        )
    return row


def load_existing_decision(
    conn: sqlite3.Connection,
    refresh_event_result_id: int,
) -> sqlite3.Row | None:
    return fetch_one(
        conn,
        """
        SELECT
            decision_id,
            refresh_event_result_id,
            event_id_snapshot,
            decision_type,
            selected_probability,
            assessment_id,
            run_id,
            decision_source,
            decided_at
        FROM refresh_user_decisions
        WHERE refresh_event_result_id = ?
        """,
        (refresh_event_result_id,),
    )


def load_newer_finalized_decision(
    conn: sqlite3.Connection,
    refresh: sqlite3.Row,
) -> sqlite3.Row | None:
    """Return the newest finalized refresh that supersedes this refresh."""
    return fetch_one(
        conn,
        """
        SELECT
            newer.refresh_event_result_id,
            newer.refresh_id,
            decision.decision_id,
            decision.decision_type,
            decision.selected_probability,
            decision.decided_at
        FROM refresh_event_results newer
        JOIN refresh_user_decisions decision
          ON decision.refresh_event_result_id = newer.refresh_event_result_id
        WHERE newer.event_id = ?
          AND (
                newer.refresh_id > ?
                OR (
                    newer.refresh_id = ?
                    AND newer.refresh_event_result_id > ?
                )
          )
        ORDER BY
            newer.refresh_id DESC,
            newer.refresh_event_result_id DESC
        LIMIT 1
        """,
        (
            str(refresh["event_id"]),
            int(refresh["refresh_id"]),
            int(refresh["refresh_id"]),
            int(refresh["refresh_event_result_id"]),
        ),
    )


def reject_if_superseded(
    conn: sqlite3.Connection,
    refresh: sqlite3.Row,
) -> None:
    newer = load_newer_finalized_decision(conn, refresh)
    if newer is None:
        return

    raise CliError(
        "stale refresh: superseded by finalized "
        f"refresh_event_result_id={int(newer['refresh_event_result_id'])} "
        f"refresh_id={int(newer['refresh_id'])} "
        f"decision_id={int(newer['decision_id'])} "
        f"decision_type={newer['decision_type']}"
    )


def load_accepted_candidates(
    conn: sqlite3.Connection,
    refresh_event_result_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            candidate_id,
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
            validation_state
        FROM refresh_candidate_evidence
        WHERE refresh_event_result_id = ?
          AND validation_state = 'accepted'
        ORDER BY ordinal, candidate_id
        """,
        (refresh_event_result_id,),
    ).fetchall()


def validate_refresh_is_actionable(
    refresh: sqlite3.Row,
    current: sqlite3.Row | None,
) -> None:
    if refresh["job_state"] != "completed":
        raise CliError(
            f"refresh result is not completed: job_state={refresh['job_state']!r}"
        )
    if int(refresh["recommendation_valid"] or 0) != 1:
        raise CliError("refresh recommendation is invalid and cannot be applied")
    if refresh["event_id"] is None:
        raise CliError("refresh result is detached from an event")
    if refresh["baseline_assessment_id"] is None:
        raise CliError("refresh result has no baseline_assessment_id")
    if refresh["baseline_probability"] is None:
        raise CliError("refresh result has no baseline_probability")
    if current is None:
        raise CliError("event has no current official assessment")

    baseline_id = int(refresh["baseline_assessment_id"])
    current_id = int(current["assessment_id"])
    if current_id != baseline_id:
        raise CliError(
            "stale recommendation: "
            f"baseline_assessment_id={baseline_id}, current_assessment_id={current_id}"
        )

    baseline_probability = int(refresh["baseline_probability"])
    current_probability = int(current["probability_percent"])
    if current_probability != baseline_probability:
        raise CliError(
            "stale recommendation probability: "
            f"baseline_probability={baseline_probability}, "
            f"current_probability={current_probability}"
        )


def validate_candidate_for_promotion(candidate: sqlite3.Row) -> None:
    candidate_id = int(candidate["candidate_id"])

    if candidate["validation_state"] != "accepted":
        raise CliError(
            f"candidate_id={candidate_id} is not quarantine-accepted"
        )

    url = (candidate["url"] or "").strip()
    if not url:
        raise CliError(f"candidate_id={candidate_id} has no URL")

    summary = (candidate["summary"] or "").strip()
    if not summary:
        raise CliError(f"candidate_id={candidate_id} has no summary")

    direction = (candidate["direction"] or "").strip()
    if direction not in {"indicator", "counterindicator", "neutral"}:
        raise CliError(
            f"candidate_id={candidate_id} has invalid direction={direction!r}"
        )

    strength = candidate["strength"]
    if strength is not None and str(strength).strip() not in {
        "weak",
        "medium",
        "strong",
    }:
        raise CliError(
            f"candidate_id={candidate_id} has invalid strength={strength!r}"
        )

    for field in ("relevance", "credibility"):
        value = candidate[field]
        if value is not None and not 0 <= int(value) <= 100:
            raise CliError(
                f"candidate_id={candidate_id} has invalid {field}={value!r}"
            )


def resolve_selected_probability(
    args: argparse.Namespace,
    refresh: sqlite3.Row,
    current: sqlite3.Row,
) -> int:
    if args.decision == DECISION_ACCEPT:
        if args.probability is not None:
            raise CliError("--probability is only allowed with custom_probability")
        if int(refresh["change_recommended"] or 0) != 1:
            raise CliError("refresh result does not recommend a probability change")
        if refresh["recommended_probability"] is None:
            raise CliError("refresh result has no recommended_probability")
        return int(refresh["recommended_probability"])

    if args.decision == DECISION_CUSTOM:
        if args.probability is None:
            raise CliError("--probability is required with custom_probability")
        if not 0 <= args.probability <= 100:
            raise CliError("--probability must be between 0 and 100")
        return int(args.probability)

    if args.decision == DECISION_KEEP:
        if args.probability is not None:
            raise CliError("--probability is not allowed with keep_current")
        return int(current["probability_percent"])

    raise CliError(f"unsupported decision: {args.decision}")


def create_run(
    conn: sqlite3.Connection,
    refresh_event_result_id: int,
    decision_type: str,
    source: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(run_type, status, notes)
        VALUES ('manual_cli', 'running', ?)
        """,
        (
            "Finalize explicit PROROK refresh user decision "
            f"refresh_event_result_id={refresh_event_result_id} "
            f"decision_type={decision_type} source={source}",
        ),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    new_sources_found: int,
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET finished_at = ?,
            status = 'completed',
            events_processed = 1,
            new_sources_found = ?
        WHERE run_id = ?
        """,
        (utc_now(), new_sources_found, run_id),
    )


def build_rationale(
    refresh: sqlite3.Row,
    decision_type: str,
    source: str,
    selected_probability: int,
) -> str:
    baseline = int(refresh["baseline_probability"])
    recommended = refresh["recommended_probability"]
    recommended_text = "n/a" if recommended is None else f"{int(recommended)}%"
    text = (
        "Explicit user decision "
        f"via {source} for refresh_event_result_id="
        f"{int(refresh['refresh_event_result_id'])}. "
        f"decision={decision_type}; baseline={baseline}%; "
        f"recommended={recommended_text}; selected={selected_probability}%."
    )
    reason = (refresh["recommendation_reason"] or "").strip()
    if reason:
        text += f" Refresh recommendation reason: {reason}"
    return text


def assert_candidate_source_not_already_official(
    conn: sqlite3.Connection,
    *,
    candidate_id: int,
    event_id: str,
    raw_url: str,
) -> None:
    """Fail closed when this canonical source already exists for the event."""
    _canonical_url, canonical_hash, _domain = canonicalize_url(raw_url)
    existing = fetch_one(
        conn,
        """
        SELECT e.evidence_id, e.source_id
        FROM evidence_items e
        JOIN sources s ON s.source_id = e.source_id
        WHERE e.event_id = ?
          AND s.canonical_url_hash = ?
        ORDER BY e.evidence_id
        LIMIT 1
        """,
        (event_id, canonical_hash),
    )
    if existing is not None:
        raise CliError(
            "candidate source already exists as official evidence for this event: "
            f"candidate_id={candidate_id} evidence_id={existing['evidence_id']} "
            f"source_id={existing['source_id']}"
        )


def upsert_candidate_source(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    *,
    now: str,
) -> tuple[int, bool]:
    candidate_id = int(candidate["candidate_id"])
    raw_url = str(candidate["url"]).strip()
    canonical_url, canonical_hash, domain = canonicalize_url(raw_url)

    existed = fetch_one(
        conn,
        "SELECT source_id FROM sources WHERE canonical_url_hash = ?",
        (canonical_hash,),
    ) is not None

    metadata = json.dumps(
        {
            "promotion": "refresh_user_decision",
            "refresh_candidate_id": candidate_id,
            "refresh_event_result_id": int(candidate["refresh_event_result_id"]),
            "source_label": candidate["source"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    conn.execute(
        """
        INSERT INTO sources(
            url,
            canonical_url,
            canonical_url_hash,
            title,
            domain,
            published_at,
            first_seen_at,
            last_seen_at,
            source_type,
            raw_metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'web', ?)
        ON CONFLICT(canonical_url_hash) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            title = COALESCE(sources.title, excluded.title),
            published_at = COALESCE(sources.published_at, excluded.published_at),
            source_type = COALESCE(sources.source_type, excluded.source_type),
            raw_metadata = COALESCE(sources.raw_metadata, excluded.raw_metadata)
        """,
        (
            raw_url,
            canonical_url,
            canonical_hash,
            candidate["title"],
            domain,
            candidate["published_at"],
            now,
            now,
            metadata,
        ),
    )

    source = fetch_one(
        conn,
        "SELECT source_id FROM sources WHERE canonical_url_hash = ?",
        (canonical_hash,),
    )
    if source is None:
        raise CliError(f"source upsert failed for candidate_id={candidate_id}")

    return int(source["source_id"]), not existed


def promote_candidate(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    *,
    event_id: str,
    decision_id: int,
    run_id: int,
    now: str,
) -> tuple[int, str, bool]:
    validate_candidate_for_promotion(candidate)

    candidate_id = int(candidate["candidate_id"])
    assert_candidate_source_not_already_official(
        conn,
        candidate_id=candidate_id,
        event_id=event_id,
        raw_url=str(candidate["url"]).strip(),
    )
    source_id, new_source = upsert_candidate_source(
        conn,
        candidate,
        now=now,
    )

    summary = str(candidate["summary"]).strip()
    direction = str(candidate["direction"]).strip()
    strength = (
        None
        if candidate["strength"] is None
        else str(candidate["strength"]).strip()
    )

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO evidence_items(
            event_id,
            source_id,
            run_id,
            direction,
            strength,
            summary,
            relevance,
            credibility,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            source_id,
            run_id,
            direction,
            strength,
            summary,
            candidate["relevance"],
            candidate["credibility"],
            now,
        ),
    )

    if cur.rowcount == 1:
        evidence_id = int(cur.lastrowid)
        promotion_action = "inserted"
    else:
        evidence = fetch_one(
            conn,
            """
            SELECT evidence_id
            FROM evidence_items
            WHERE event_id = ?
              AND source_id = ?
              AND direction = ?
              AND summary = ?
            """,
            (event_id, source_id, direction, summary),
        )
        if evidence is None:
            raise CliError(
                f"evidence deduplication lookup failed for candidate_id={candidate_id}"
            )
        evidence_id = int(evidence["evidence_id"])
        promotion_action = "reused"

    conn.execute(
        """
        INSERT INTO refresh_candidate_promotions(
            candidate_id,
            refresh_event_result_id,
            decision_id,
            evidence_id,
            run_id,
            promotion_action,
            promoted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            int(candidate["refresh_event_result_id"]),
            decision_id,
            evidence_id,
            run_id,
            promotion_action,
            now,
        ),
    )

    return evidence_id, promotion_action, new_source


def print_existing_decision(
    conn: sqlite3.Connection,
    existing: sqlite3.Row,
) -> None:
    promotion_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM refresh_candidate_promotions
        WHERE decision_id = ?
        """,
        (int(existing["decision_id"]),),
    ).fetchone()[0]

    print("OK: decision already applied")
    print(f"decision_id: {existing['decision_id']}")
    print(f"refresh_event_result_id: {existing['refresh_event_result_id']}")
    print(f"event_id: {existing['event_id_snapshot']}")
    print(f"decision_type: {existing['decision_type']}")
    print(f"selected_probability: {existing['selected_probability']}%")
    print(
        "assessment_id: "
        f"{existing['assessment_id'] if existing['assessment_id'] is not None else 'none'}"
    )
    print(
        "run_id: "
        f"{existing['run_id'] if existing['run_id'] is not None else 'none'}"
    )
    print(f"promoted_candidates: {promotion_count}")
    print(f"decision_source: {existing['decision_source']}")
    print(f"decided_at: {existing['decided_at']}")
    print("idempotent_replay: true")


def cmd_apply(args: argparse.Namespace) -> int:
    db_path = resolve_db(args)
    now = utc_now()

    with connect(db_path) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            require_schema_v8(conn)

            refresh = load_refresh_result(conn, args.refresh_event_result_id)
            event_id = refresh["event_id"]
            if event_id is None:
                raise CliError("refresh result is detached from an event")

            existing = load_existing_decision(conn, args.refresh_event_result_id)
            if existing is not None:
                if args.decision == DECISION_CUSTOM:
                    if args.probability is None:
                        raise CliError("--probability is required with custom_probability")
                    requested_probability = int(args.probability)
                elif args.decision == DECISION_ACCEPT:
                    if args.probability is not None:
                        raise CliError("--probability is only allowed with custom_probability")
                    if refresh["recommended_probability"] is None:
                        raise CliError("refresh result has no recommended_probability")
                    requested_probability = int(refresh["recommended_probability"])
                else:
                    if args.probability is not None:
                        raise CliError("--probability is not allowed with keep_current")
                    requested_probability = int(existing["selected_probability"])

                same_request = (
                    existing["decision_type"] == args.decision
                    and int(existing["selected_probability"]) == requested_probability
                )
                if not same_request:
                    raise CliError(
                        "refresh result already has a different final decision: "
                        f"decision_id={existing['decision_id']} "
                        f"decision_type={existing['decision_type']} "
                        f"selected_probability={existing['selected_probability']}"
                    )
                conn.rollback()
                print_existing_decision(conn, existing)
                return 0

            # A later finalized refresh supersedes every earlier undecided
            # refresh for the same event, even when keep_current left the
            # baseline assessment id/probability unchanged.
            reject_if_superseded(conn, refresh)

            current = current_assessment(conn, str(event_id))
            validate_refresh_is_actionable(refresh, current)
            assert current is not None

            selected_probability = resolve_selected_probability(args, refresh, current)

            # Enforce the existing PROROK probability scale without inventing
            # labels for the intentional gaps between defined ranges.
            band, label = map_probability(selected_probability)

            accepted_candidates = load_accepted_candidates(
                conn,
                int(refresh["refresh_event_result_id"]),
            )
            for candidate in accepted_candidates:
                validate_candidate_for_promotion(candidate)

            run_id = create_run(
                conn,
                int(refresh["refresh_event_result_id"]),
                args.decision,
                args.source,
            )

            assessment_id: int | None = None
            if args.decision != DECISION_KEEP:
                previous_probability = int(current["probability_percent"])
                delta = selected_probability - previous_probability
                rationale = build_rationale(
                    refresh,
                    args.decision,
                    args.source,
                    selected_probability,
                )

                cur = conn.execute(
                    """
                    INSERT INTO assessments(
                        event_id,
                        run_id,
                        assessed_at,
                        probability_percent,
                        probability_band,
                        probability_label,
                        confidence,
                        delta_from_previous,
                        rationale
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event_id),
                        run_id,
                        now,
                        selected_probability,
                        band,
                        label,
                        current["confidence"],
                        delta,
                        rationale,
                    ),
                )
                assessment_id = int(cur.lastrowid)

            cur = conn.execute(
                """
                INSERT INTO refresh_user_decisions(
                    refresh_event_result_id,
                    event_id_snapshot,
                    decision_type,
                    baseline_assessment_id,
                    baseline_probability,
                    recommended_probability,
                    selected_probability,
                    assessment_id,
                    run_id,
                    decision_source,
                    decided_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(refresh["refresh_event_result_id"]),
                    str(event_id),
                    args.decision,
                    int(refresh["baseline_assessment_id"]),
                    int(refresh["baseline_probability"]),
                    (
                        None
                        if refresh["recommended_probability"] is None
                        else int(refresh["recommended_probability"])
                    ),
                    selected_probability,
                    assessment_id,
                    run_id,
                    args.source,
                    now,
                ),
            )
            decision_id = int(cur.lastrowid)

            promoted_count = 0
            inserted_evidence_count = 0
            reused_evidence_count = 0
            new_sources_found = 0

            for candidate in accepted_candidates:
                _evidence_id, action, new_source = promote_candidate(
                    conn,
                    candidate,
                    event_id=str(event_id),
                    decision_id=decision_id,
                    run_id=run_id,
                    now=now,
                )
                promoted_count += 1
                if action == "inserted":
                    inserted_evidence_count += 1
                else:
                    reused_evidence_count += 1
                if new_source:
                    new_sources_found += 1

            finish_run(
                conn,
                run_id,
                new_sources_found=new_sources_found,
            )

            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise CliError(
                    f"foreign_key_check failed with {len(fk_errors)} error(s)"
                )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

    print("OK: PROROK refresh decision finalized")
    print(f"decision_id: {decision_id}")
    print(f"refresh_event_result_id: {args.refresh_event_result_id}")
    print(f"event_id: {event_id}")
    print(f"decision_type: {args.decision}")
    print(f"baseline_probability: {refresh['baseline_probability']}%")
    print(
        "recommended_probability: "
        f"{refresh['recommended_probability']}%"
        if refresh["recommended_probability"] is not None
        else "recommended_probability: n/a"
    )
    print(f"selected_probability: {selected_probability}%")
    print(f"band: {band}")
    print(f"label: {label}")
    print(
        "assessment_id: "
        f"{assessment_id if assessment_id is not None else 'none'}"
    )
    print(f"run_id: {run_id}")
    print(f"accepted_candidates: {len(accepted_candidates)}")
    print(f"promoted_candidates: {promoted_count}")
    print(f"official_evidence_inserted: {inserted_evidence_count}")
    print(f"official_evidence_reused: {reused_evidence_count}")
    print(f"new_sources_found: {new_sources_found}")
    print(
        "confidence: "
        f"{current['confidence'] if current['confidence'] is not None else 'n/a'}"
    )
    print(f"decision_source: {args.source}")
    print(f"decided_at: {now}")
    print("atomic_commit: true")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply an explicit user decision to a PROROK refresh result"
    )
    parser.add_argument("--home", help="PROROK data directory")
    parser.add_argument("--db", help="SQLite DB path")

    sub = parser.add_subparsers(dest="command", required=True)

    apply_cmd = sub.add_parser(
        "apply",
        help="Atomically finalize one refresh decision and promote accepted evidence",
    )
    apply_cmd.add_argument(
        "refresh_event_result_id",
        type=int,
        help="refresh_event_results.refresh_event_result_id",
    )
    apply_cmd.add_argument(
        "--decision",
        required=True,
        choices=DECISION_TYPES,
    )
    apply_cmd.add_argument(
        "--probability",
        type=int,
        help="Required only for --decision custom_probability",
    )
    apply_cmd.add_argument(
        "--source",
        default="telegram",
        choices=SOURCE_CHOICES,
        help="Audit source for the explicit user decision",
    )
    apply_cmd.set_defaults(func=cmd_apply)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.IntegrityError as exc:
        print(f"SQLITE_INTEGRITY_ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"SQLITE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
