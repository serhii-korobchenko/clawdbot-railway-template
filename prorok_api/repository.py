from __future__ import annotations

import sqlite3
from typing import Any

from .parsers import parse_decision_criteria, parse_tags


def _current_assessment(row: sqlite3.Row) -> dict[str, Any] | None:
    if row["assessment_id"] is None:
        return None

    return {
        "assessment_id": row["assessment_id"],
        "assessed_at": row["assessed_at"],
        "probability_percent": row["probability_percent"],
        "probability_band": row["probability_band"],
        "probability_label": row["probability_label"],
        "confidence": row["confidence"],
        "delta_from_previous": row["delta_from_previous"],
    }


def list_events(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []

    if status:
        clauses.append("e.status = ?")
        params.append(status)

    if q and q.strip():
        needle = f"%{q.strip().casefold()}%"
        clauses.append(
            "("
            "CASEFOLD(e.title) LIKE ? OR "
            "CASEFOLD(e.question) LIKE ? OR "
            "CASEFOLD(e.event_id) LIKE ? OR "
            "CASEFOLD(COALESCE(e.tags, '')) LIKE ?"
            ")"
        )
        params.extend([needle, needle, needle, needle])

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""

    sql = f"""
        WITH assessment_counts AS (
            SELECT event_id, COUNT(*) AS assessment_count
            FROM assessments
            GROUP BY event_id
        ),
        evidence_counts AS (
            SELECT
                event_id,
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT source_id) AS source_count
            FROM evidence_items
            GROUP BY event_id
        )
        SELECT
            e.event_id,
            e.title,
            e.question,
            e.status,
            e.forecast_horizon,
            e.created_at,
            e.updated_at,
            e.archived_at,
            les.assessment_id,
            les.assessed_at,
            les.probability_percent,
            les.probability_band,
            les.probability_label,
            les.confidence,
            les.delta_from_previous,
            COALESCE(ac.assessment_count, 0) AS assessment_count,
            COALESCE(ec.evidence_count, 0) AS evidence_count,
            COALESCE(ec.source_count, 0) AS source_count
        FROM events e
        LEFT JOIN latest_event_state les
            ON les.event_id = e.event_id
        LEFT JOIN assessment_counts ac
            ON ac.event_id = e.event_id
        LEFT JOIN evidence_counts ec
            ON ec.event_id = e.event_id
        {where_sql}
        ORDER BY
            CASE e.status
                WHEN 'active' THEN 1
                WHEN 'paused' THEN 2
                WHEN 'resolved' THEN 3
                WHEN 'archived' THEN 4
                ELSE 5
            END,
            COALESCE(les.assessed_at, e.updated_at) DESC,
            e.event_id ASC
    """

    rows = conn.execute(sql, params).fetchall()

    items = []
    for row in rows:
        items.append({
            "event_id": row["event_id"],
            "title": row["title"],
            "question": row["question"],
            "status": row["status"],
            "forecast_horizon": row["forecast_horizon"],
            "current_assessment": _current_assessment(row),
            "assessment_count": row["assessment_count"],
            "evidence_count": row["evidence_count"],
            "source_count": row["source_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived_at": row["archived_at"],
        })

    status_counts = {
        "active": 0,
        "paused": 0,
        "resolved": 0,
        "archived": 0,
    }
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM events GROUP BY status"
    ).fetchall():
        if row["status"] in status_counts:
            status_counts[row["status"]] = row["n"]

    return {
        "items": items,
        "total": sum(status_counts.values()),
        "filtered_total": len(items),
        "status_counts": status_counts,
    }


def get_event_detail(
    conn: sqlite3.Connection,
    event_id: str,
) -> dict[str, Any] | None:
    event = conn.execute(
        """
        SELECT
            event_id,
            title,
            question,
            status,
            forecast_horizon,
            decision_criteria,
            tags,
            source_image_note,
            created_at,
            updated_at,
            archived_at
        FROM events
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()

    if event is None:
        return None

    latest = conn.execute(
        """
        SELECT
            assessment_id,
            assessed_at,
            probability_percent,
            probability_band,
            probability_label,
            confidence,
            delta_from_previous
        FROM assessments
        WHERE event_id = ?
        ORDER BY assessed_at DESC, assessment_id DESC
        LIMIT 1
        """,
        (event_id,),
    ).fetchone()

    assessment_rows = conn.execute(
        """
        SELECT
            assessment_id,
            run_id,
            assessed_at,
            probability_percent,
            probability_band,
            probability_label,
            confidence,
            delta_from_previous,
            rationale
        FROM assessments
        WHERE event_id = ?
        ORDER BY assessed_at ASC, assessment_id ASC
        """,
        (event_id,),
    ).fetchall()

    evidence_rows = conn.execute(
        """
        SELECT
            ei.evidence_id,
            ei.run_id,
            ei.created_at,
            ei.direction,
            ei.strength,
            ei.summary,
            ei.relevance,
            ei.credibility,
            s.source_id,
            s.title AS source_title,
            s.domain,
            s.url,
            s.canonical_url,
            s.published_at,
            s.source_type
        FROM evidence_items ei
        JOIN sources s
          ON s.source_id = ei.source_id
        WHERE ei.event_id = ?
        ORDER BY ei.created_at ASC, ei.evidence_id ASC
        """,
        (event_id,),
    ).fetchall()

    current = _current_assessment(latest) if latest is not None else None

    assessments = [
        {
            "assessment_id": row["assessment_id"],
            "run_id": row["run_id"],
            "assessed_at": row["assessed_at"],
            "probability_percent": row["probability_percent"],
            "probability_band": row["probability_band"],
            "probability_label": row["probability_label"],
            "confidence": row["confidence"],
            "delta_from_previous": row["delta_from_previous"],
            "rationale": row["rationale"],
        }
        for row in assessment_rows
    ]

    evidence = [
        {
            "evidence_id": row["evidence_id"],
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "direction": row["direction"],
            "strength": row["strength"],
            "summary": row["summary"],
            "relevance": row["relevance"],
            "credibility": row["credibility"],
            "source": {
                "source_id": row["source_id"],
                "title": row["source_title"],
                "domain": row["domain"],
                "url": row["url"],
                "canonical_url": row["canonical_url"],
                "published_at": row["published_at"],
                "source_type": row["source_type"],
            },
        }
        for row in evidence_rows
    ]

    return {
        "event": {
            "event_id": event["event_id"],
            "title": event["title"],
            "question": event["question"],
            "status": event["status"],
            "forecast_horizon": event["forecast_horizon"],
            "tags": parse_tags(event["tags"]),
            "decision_criteria": parse_decision_criteria(
                event["decision_criteria"]
            ),
            "provenance": {
                "source_image_note": event["source_image_note"],
            },
            "created_at": event["created_at"],
            "updated_at": event["updated_at"],
            "archived_at": event["archived_at"],
        },
        "current_assessment": current,
        "assessments": assessments,
        "evidence": evidence,
        "limitations": {
            "assessment_evidence_attribution": "unavailable",
        },
    }


def get_latest_recommendation(
    conn: sqlite3.Connection,
    event_id: str,
) -> dict[str, Any] | None:
    event = conn.execute(
        "SELECT event_id FROM events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if event is None:
        return None

    row = conn.execute(
        """
        SELECT
            rer.refresh_event_result_id,
            rer.refresh_id,
            rer.created_at,
            rer.outcome,
            rer.baseline_assessment_id,
            rer.baseline_probability,
            rer.recommended_probability,
            rer.recommended_band,
            rer.recommended_label,
            rer.recommendation_confidence,
            rer.recommendation_reason,
            rer.change_recommended,
            rer.candidate_rejected_count,
            rer.recommendation_valid,
            current.assessment_id AS current_assessment_id,
            current.probability_percent AS current_probability,
            d.decision_id,
            d.decision_type,
            d.selected_probability,
            d.assessment_id AS decision_assessment_id,
            d.decision_source,
            d.decided_at
        FROM refresh_event_results rer
        LEFT JOIN assessments current
          ON current.assessment_id = (
              SELECT a.assessment_id
              FROM assessments a
              WHERE a.event_id = rer.event_id
              ORDER BY a.assessed_at DESC, a.assessment_id DESC
              LIMIT 1
          )
        LEFT JOIN refresh_user_decisions d
          ON d.refresh_event_result_id = rer.refresh_event_result_id
        WHERE rer.event_id = ?
          AND rer.job_state = 'completed'
          AND rer.recommendation_valid = 1
          AND rer.recommended_probability IS NOT NULL
        ORDER BY rer.created_at DESC, rer.refresh_event_result_id DESC
        LIMIT 1
        """,
        (event_id,),
    ).fetchone()

    if row is None:
        return {
            "event_id": event_id,
            "recommendation": None,
        }

    baseline_assessment_id = int(row["baseline_assessment_id"])
    baseline_probability = int(row["baseline_probability"])
    current_assessment_id = row["current_assessment_id"]
    current_probability = row["current_probability"]

    is_stale = (
        current_assessment_id is None
        or current_probability is None
        or int(current_assessment_id) != baseline_assessment_id
        or int(current_probability) != baseline_probability
    )

    decision = None
    if row["decision_id"] is not None:
        decision = {
            "decision_id": row["decision_id"],
            "decision_type": row["decision_type"],
            "selected_probability": row["selected_probability"],
            "assessment_id": row["decision_assessment_id"],
            "decision_source": row["decision_source"],
            "decided_at": row["decided_at"],
        }

    change_recommended = bool(row["change_recommended"])
    actionable = decision is None and not is_stale and change_recommended

    if decision is not None:
        status = "decided"
    elif is_stale:
        status = "stale"
    elif change_recommended:
        status = "actionable"
    else:
        status = "no_change"

    return {
        "event_id": event_id,
        "recommendation": {
            "refresh_event_result_id": row["refresh_event_result_id"],
            "refresh_id": row["refresh_id"],
            "created_at": row["created_at"],
            "outcome": row["outcome"],
            "baseline_assessment_id": baseline_assessment_id,
            "baseline_probability": baseline_probability,
            "recommended_probability": int(row["recommended_probability"]),
            "recommended_band": row["recommended_band"],
            "recommended_label": row["recommended_label"],
            "recommendation_confidence": row["recommendation_confidence"],
            "recommendation_reason": row["recommendation_reason"],
            "change_recommended": change_recommended,
            "candidate_rejected_count": int(row["candidate_rejected_count"] or 0),
            "recommendation_valid": bool(row["recommendation_valid"]),
            "current_assessment_id": current_assessment_id,
            "current_probability": current_probability,
            "is_stale": is_stale,
            "actionable": actionable,
            "status": status,
            "decision": decision,
        },
    }
