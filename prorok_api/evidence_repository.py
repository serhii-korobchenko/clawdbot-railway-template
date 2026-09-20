from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


_ACTIVITY_WINDOWS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def evidence_activity(conn: sqlite3.Connection, *, status: str = "active", window: str = "7d", now: datetime | None = None) -> dict[str, Any]:
    if window not in _ACTIVITY_WINDOWS:
        raise ValueError(f"Unsupported evidence activity window: {window}")
    generated = now or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    generated = generated.astimezone(timezone.utc)
    duration = _ACTIVITY_WINDOWS[window]
    cutoff = generated - duration if duration is not None else None

    join_filter = ""
    params: list[Any] = []
    if cutoff is not None:
        join_filter = " AND julianday(ei.created_at) >= julianday(?)"
        params.append(_iso_utc(cutoff))
    params.append(status)

    rows = conn.execute(
        f"""
        SELECT e.event_id, e.title, e.status,
               COUNT(ei.evidence_id) AS evidence_count,
               SUM(CASE WHEN ei.direction = 'indicator' THEN 1 ELSE 0 END) AS indicator_count,
               SUM(CASE WHEN ei.direction = 'counterindicator' THEN 1 ELSE 0 END) AS counterindicator_count,
               SUM(CASE WHEN ei.direction = 'neutral' THEN 1 ELSE 0 END) AS neutral_count,
               MAX(strftime('%Y-%m-%dT%H:%M:%fZ', ei.created_at)) AS latest_evidence_at
        FROM events e
        LEFT JOIN evidence_items ei
          ON ei.event_id = e.event_id
          {join_filter}
        WHERE e.status = ?
        GROUP BY e.event_id, e.title, e.status
        ORDER BY evidence_count DESC, latest_evidence_at DESC, e.title ASC
        """,
        params,
    ).fetchall()

    items = [{
        "event_id": row["event_id"],
        "title": row["title"],
        "status": row["status"],
        "evidence_count": int(row["evidence_count"] or 0),
        "indicator_count": int(row["indicator_count"] or 0),
        "counterindicator_count": int(row["counterindicator_count"] or 0),
        "neutral_count": int(row["neutral_count"] or 0),
        "latest_evidence_at": row["latest_evidence_at"],
    } for row in rows]

    return {
        "status": status,
        "window": window,
        "generated_at": _iso_utc(generated),
        "cutoff_at": _iso_utc(cutoff) if cutoff is not None else None,
        "total_events": len(items),
        "total_evidence": sum(item["evidence_count"] for item in items),
        "items": items,
    }


def list_evidence(
    conn: sqlite3.Connection,
    *,
    event_id: str | None = None,
    direction: str | None = None,
    strength: str | None = None,
    source: str | None = None,
    q: str | None = None,
    sort: str = "newest",
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []

    if event_id:
        clauses.append("ei.event_id = ?")
        params.append(event_id)
    if direction:
        clauses.append("ei.direction = ?")
        params.append(direction)
    if strength:
        clauses.append("ei.strength = ?")
        params.append(strength)
    if source and source.strip():
        needle = f"%{source.strip().casefold()}%"
        clauses.append(
            "(CASEFOLD(COALESCE(s.domain, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(s.title, '')) LIKE ? OR "
            "CASEFOLD(s.url) LIKE ? OR "
            "CASEFOLD(COALESCE(s.source_type, '')) LIKE ?)"
        )
        params.extend([needle, needle, needle, needle])
    if q and q.strip():
        needle = f"%{q.strip().casefold()}%"
        clauses.append(
            "(CASEFOLD(ei.summary) LIKE ? OR "
            "CASEFOLD(e.title) LIKE ? OR "
            "CASEFOLD(e.event_id) LIKE ? OR "
            "CASEFOLD(COALESCE(s.title, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(s.domain, '')) LIKE ? OR "
            "CASEFOLD(s.url) LIKE ?)"
        )
        params.extend([needle, needle, needle, needle, needle, needle])

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_direction = "ASC" if sort == "oldest" else "DESC"
    rows = conn.execute(
        f"""
        SELECT
            ei.evidence_id,
            ei.run_id,
            ei.created_at,
            ei.direction,
            ei.strength,
            ei.summary,
            ei.relevance,
            ei.credibility,
            e.event_id,
            e.title AS event_title,
            e.status AS event_status,
            s.source_id,
            s.title AS source_title,
            s.domain,
            s.url,
            s.canonical_url,
            s.published_at,
            s.source_type,
            p.refresh_event_result_id,
            rer.refresh_id,
            d.decision_id,
            d.decision_type,
            d.baseline_probability,
            d.selected_probability,
            d.assessment_id,
            d.decided_at
        FROM evidence_items ei
        JOIN events e ON e.event_id = ei.event_id
        JOIN sources s ON s.source_id = ei.source_id
        LEFT JOIN refresh_candidate_promotions p ON p.evidence_id = ei.evidence_id
        LEFT JOIN refresh_user_decisions d ON d.decision_id = p.decision_id
        LEFT JOIN refresh_event_results rer ON rer.refresh_event_result_id = p.refresh_event_result_id
        {where_sql}
        ORDER BY ei.created_at {order_direction}, ei.evidence_id {order_direction}
        """,
        params,
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) AS n FROM evidence_items").fetchone()["n"]
    items = [
        {
            "evidence_id": row["evidence_id"],
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "direction": row["direction"],
            "strength": row["strength"],
            "summary": row["summary"],
            "relevance": row["relevance"],
            "credibility": row["credibility"],
            "event": {
                "event_id": row["event_id"],
                "title": row["event_title"],
                "status": row["event_status"],
            },
            "source": {
                "source_id": row["source_id"],
                "title": row["source_title"],
                "domain": row["domain"],
                "url": row["url"],
                "canonical_url": row["canonical_url"],
                "published_at": row["published_at"],
                "source_type": row["source_type"],
            },
            "assessment": {
                "status": (
                    "unknown"
                    if row["decision_id"] is None
                    else (
                        "assessed_unchanged"
                        if int(row["selected_probability"]) == int(row["baseline_probability"])
                        else "assessed_changed"
                    )
                ),
                "refresh_id": row["refresh_id"],
                "refresh_event_result_id": row["refresh_event_result_id"],
                "decision_id": row["decision_id"],
                "decision_type": row["decision_type"],
                "baseline_probability": row["baseline_probability"],
                "selected_probability": row["selected_probability"],
                "assessment_id": row["assessment_id"],
                "decided_at": row["decided_at"],
            },
        }
        for row in rows
    ]
    return {"items": items, "total": int(total), "filtered_total": len(items)}


def list_candidate_evidence(
    conn: sqlite3.Connection,
    *,
    event_id: str | None = None,
    direction: str | None = None,
    strength: str | None = None,
    validation_state: str | None = None,
    source: str | None = None,
    q: str | None = None,
    sort: str = "newest",
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []

    if event_id:
        clauses.append("rer.event_id = ?")
        params.append(event_id)
    if direction:
        clauses.append("c.direction = ?")
        params.append(direction)
    if strength:
        clauses.append("c.strength = ?")
        params.append(strength)
    if validation_state:
        clauses.append("c.validation_state = ?")
        params.append(validation_state)
    if source and source.strip():
        needle = f"%{source.strip().casefold()}%"
        clauses.append(
            "(CASEFOLD(COALESCE(c.source, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(c.title, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(c.url, '')) LIKE ?)"
        )
        params.extend([needle, needle, needle])
    if q and q.strip():
        needle = f"%{q.strip().casefold()}%"
        clauses.append(
            "(CASEFOLD(COALESCE(c.summary, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(c.why_it_matters, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(c.title, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(c.source, '')) LIKE ? OR "
            "CASEFOLD(COALESCE(c.url, '')) LIKE ? OR "
            "CASEFOLD(rer.event_title_snapshot) LIKE ? OR "
            "CASEFOLD(COALESCE(rer.event_id, '')) LIKE ?)"
        )
        params.extend([needle, needle, needle, needle, needle, needle, needle])

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_direction = "ASC" if sort == "oldest" else "DESC"
    rows = conn.execute(
        f"""
        SELECT
            c.candidate_id,
            c.refresh_event_result_id,
            rer.refresh_id,
            rer.event_id,
            rer.event_title_snapshot,
            c.ordinal,
            c.direction,
            c.strength,
            c.relevance,
            c.credibility,
            c.title,
            c.source,
            c.url,
            c.published_at,
            c.summary,
            c.why_it_matters,
            c.duplicate_risk,
            c.freshness,
            c.validation_state,
            c.rejection_reason,
            c.created_at,
            d.decision_id,
            d.decision_type,
            d.selected_probability,
            d.decided_at,
            p.promotion_action,
            p.evidence_id AS promoted_evidence_id,
            p.promoted_at
        FROM refresh_candidate_evidence c
        JOIN refresh_event_results rer
          ON rer.refresh_event_result_id = c.refresh_event_result_id
        LEFT JOIN refresh_user_decisions d
          ON d.refresh_event_result_id = c.refresh_event_result_id
        LEFT JOIN refresh_candidate_promotions p
          ON p.candidate_id = c.candidate_id
        {where_sql}
        ORDER BY c.created_at {order_direction}, c.candidate_id {order_direction}
        """,
        params,
    ).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM refresh_candidate_evidence"
    ).fetchone()["n"]
    items = [
        {
            "candidate_id": row["candidate_id"],
            "refresh_event_result_id": row["refresh_event_result_id"],
            "refresh_id": row["refresh_id"],
            "event_id": row["event_id"],
            "event_title": row["event_title_snapshot"],
            "ordinal": row["ordinal"],
            "direction": row["direction"],
            "strength": row["strength"],
            "relevance": row["relevance"],
            "credibility": row["credibility"],
            "title": row["title"],
            "source": row["source"],
            "url": row["url"],
            "published_at": row["published_at"],
            "summary": row["summary"],
            "why_it_matters": row["why_it_matters"],
            "duplicate_risk": row["duplicate_risk"],
            "freshness": row["freshness"],
            "validation_state": row["validation_state"],
            "rejection_reason": row["rejection_reason"],
            "created_at": row["created_at"],
            "decision_id": row["decision_id"],
            "decision_type": row["decision_type"],
            "selected_probability": row["selected_probability"],
            "decided_at": row["decided_at"],
            "promotion_action": row["promotion_action"],
            "evidence_id": row["promoted_evidence_id"],
            "promoted_at": row["promoted_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": int(total), "filtered_total": len(items)}
