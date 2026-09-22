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
        LEFT JOIN evidence_items ei ON ei.event_id = e.event_id {join_filter}
        WHERE e.status = ?
        GROUP BY e.event_id, e.title, e.status
        ORDER BY evidence_count DESC, latest_evidence_at DESC, e.title ASC
        """, params).fetchall()
    items = [{"event_id": r["event_id"], "title": r["title"], "status": r["status"], "evidence_count": int(r["evidence_count"] or 0), "indicator_count": int(r["indicator_count"] or 0), "counterindicator_count": int(r["counterindicator_count"] or 0), "neutral_count": int(r["neutral_count"] or 0), "latest_evidence_at": r["latest_evidence_at"]} for r in rows]
    return {"status": status, "window": window, "generated_at": _iso_utc(generated), "cutoff_at": _iso_utc(cutoff) if cutoff is not None else None, "total_events": len(items), "total_evidence": sum(i["evidence_count"] for i in items), "items": items}


def list_evidence(conn: sqlite3.Connection, *, event_id: str | None = None, direction: str | None = None, strength: str | None = None, source: str | None = None, q: str | None = None, sort: str = "newest") -> dict[str, Any]:
    clauses=[]; params=[]
    if event_id: clauses.append("ei.event_id = ?"); params.append(event_id)
    if direction: clauses.append("ei.direction = ?"); params.append(direction)
    if strength: clauses.append("ei.strength = ?"); params.append(strength)
    if source and source.strip():
        needle=f"%{source.strip().casefold()}%"; clauses.append("(CASEFOLD(COALESCE(s.domain, '')) LIKE ? OR CASEFOLD(COALESCE(s.title, '')) LIKE ? OR CASEFOLD(s.url) LIKE ? OR CASEFOLD(COALESCE(s.source_type, '')) LIKE ?)"); params.extend([needle]*4)
    if q and q.strip():
        needle=f"%{q.strip().casefold()}%"; clauses.append("(CASEFOLD(ei.summary) LIKE ? OR CASEFOLD(e.title) LIKE ? OR CASEFOLD(e.event_id) LIKE ? OR CASEFOLD(COALESCE(s.title, '')) LIKE ? OR CASEFOLD(COALESCE(s.domain, '')) LIKE ? OR CASEFOLD(s.url) LIKE ?)"); params.extend([needle]*6)
    where_sql="WHERE "+" AND ".join(clauses) if clauses else ""; od="ASC" if sort=="oldest" else "DESC"
    rows=conn.execute(f"""SELECT ei.evidence_id,ei.run_id,ei.created_at,ei.direction,ei.strength,ei.summary,ei.relevance,ei.credibility,e.event_id,e.title AS event_title,e.status AS event_status,s.source_id,s.title AS source_title,s.domain,s.url,s.canonical_url,s.published_at,s.source_type,p.refresh_event_result_id,rer.refresh_id,d.decision_id,d.decision_type,d.baseline_probability,d.selected_probability,d.assessment_id,d.decided_at,em.evidence_assessment_decision_id AS manual_decision_id,em.baseline_probability AS manual_baseline_probability,em.selected_probability AS manual_selected_probability,em.assessment_id AS manual_assessment_id,em.decided_at AS manual_decided_at FROM evidence_items ei JOIN events e ON e.event_id=ei.event_id JOIN sources s ON s.source_id=ei.source_id LEFT JOIN refresh_candidate_promotions p ON p.evidence_id=ei.evidence_id LEFT JOIN refresh_user_decisions d ON d.decision_id=p.decision_id LEFT JOIN refresh_event_results rer ON rer.refresh_event_result_id=p.refresh_event_result_id LEFT JOIN evidence_assessment_decisions em ON em.evidence_assessment_decision_id=(SELECT em2.evidence_assessment_decision_id FROM evidence_assessment_decisions em2 WHERE em2.evidence_id=ei.evidence_id ORDER BY em2.decided_at DESC,em2.evidence_assessment_decision_id DESC LIMIT 1) {where_sql} ORDER BY ei.created_at {od},ei.evidence_id {od}""",params).fetchall()
    total=conn.execute("SELECT COUNT(*) AS n FROM evidence_items").fetchone()["n"]
    items=[]
    for r in rows:
        manual=r["manual_decision_id"] is not None; bp=r["manual_baseline_probability"] if manual else r["baseline_probability"]; sp=r["manual_selected_probability"] if manual else r["selected_probability"]
        items.append({"evidence_id":r["evidence_id"],"run_id":r["run_id"],"created_at":r["created_at"],"direction":r["direction"],"strength":r["strength"],"summary":r["summary"],"relevance":r["relevance"],"credibility":r["credibility"],"event":{"event_id":r["event_id"],"title":r["event_title"],"status":r["event_status"]},"source":{"source_id":r["source_id"],"title":r["source_title"],"domain":r["domain"],"url":r["url"],"canonical_url":r["canonical_url"],"published_at":r["published_at"],"source_type":r["source_type"]},"assessment":{"status":("unknown" if (not manual and r["decision_id"] is None) else ("assessed_unchanged" if int(sp)==int(bp) else "assessed_changed")),"provenance_type":"evidence_manual" if manual else ("refresh" if r["decision_id"] is not None else None),"evidence_assessment_decision_id":r["manual_decision_id"],"refresh_id":None if manual else r["refresh_id"],"refresh_event_result_id":None if manual else r["refresh_event_result_id"],"decision_id":None if manual else r["decision_id"],"decision_type":"evidence_manual" if manual else r["decision_type"],"baseline_probability":bp,"selected_probability":sp,"assessment_id":r["manual_assessment_id"] if manual else r["assessment_id"],"decided_at":r["manual_decided_at"] if manual else r["decided_at"]}})
    return {"items":items,"total":int(total),"filtered_total":len(items)}


def list_candidate_evidence(conn: sqlite3.Connection, *, event_id: str | None = None, direction: str | None = None, strength: str | None = None, validation_state: str | None = None, source: str | None = None, q: str | None = None, sort: str = "newest") -> dict[str, Any]:
    clauses=[]; params=[]
    if event_id: clauses.append("rer.event_id = ?"); params.append(event_id)
    if direction: clauses.append("c.direction = ?"); params.append(direction)
    if strength: clauses.append("c.strength = ?"); params.append(strength)
    if validation_state: clauses.append("c.validation_state = ?"); params.append(validation_state)
    if source and source.strip():
        needle=f"%{source.strip().casefold()}%"; clauses.append("(CASEFOLD(COALESCE(c.source, '')) LIKE ? OR CASEFOLD(COALESCE(c.title, '')) LIKE ? OR CASEFOLD(COALESCE(c.url, '')) LIKE ?)"); params.extend([needle]*3)
    if q and q.strip():
        needle=f"%{q.strip().casefold()}%"; clauses.append("(CASEFOLD(COALESCE(c.summary, '')) LIKE ? OR CASEFOLD(COALESCE(c.why_it_matters, '')) LIKE ? OR CASEFOLD(COALESCE(c.title, '')) LIKE ? OR CASEFOLD(COALESCE(c.source, '')) LIKE ? OR CASEFOLD(COALESCE(c.url, '')) LIKE ? OR CASEFOLD(rer.event_title_snapshot) LIKE ? OR CASEFOLD(COALESCE(rer.event_id, '')) LIKE ?)"); params.extend([needle]*7)
    where_sql="WHERE "+" AND ".join(clauses) if clauses else ""; od="ASC" if sort=="oldest" else "DESC"
    rows=conn.execute(f"""SELECT c.candidate_id,c.refresh_event_result_id,rer.refresh_id,rer.event_id,rer.event_title_snapshot,c.ordinal,c.direction,c.strength,c.relevance,c.credibility,c.title,c.source,c.url,c.published_at,c.summary,c.why_it_matters,c.duplicate_risk,c.freshness,c.validation_state,c.rejection_reason,c.created_at,d.decision_id,d.decision_type,d.selected_probability,d.decided_at,p.promotion_action,p.evidence_id AS promoted_evidence_id,p.promoted_at FROM refresh_candidate_evidence c JOIN refresh_event_results rer ON rer.refresh_event_result_id=c.refresh_event_result_id LEFT JOIN refresh_user_decisions d ON d.refresh_event_result_id=c.refresh_event_result_id LEFT JOIN refresh_candidate_promotions p ON p.candidate_id=c.candidate_id {where_sql} ORDER BY c.created_at {od},c.candidate_id {od}""",params).fetchall()
    total=conn.execute("SELECT COUNT(*) AS n FROM refresh_candidate_evidence").fetchone()["n"]
    items=[{"candidate_id":r["candidate_id"],"refresh_event_result_id":r["refresh_event_result_id"],"refresh_id":r["refresh_id"],"event_id":r["event_id"],"event_title":r["event_title_snapshot"],"ordinal":r["ordinal"],"direction":r["direction"],"strength":r["strength"],"relevance":r["relevance"],"credibility":r["credibility"],"title":r["title"],"source":r["source"],"url":r["url"],"published_at":r["published_at"],"summary":r["summary"],"why_it_matters":r["why_it_matters"],"duplicate_risk":r["duplicate_risk"],"freshness":r["freshness"],"validation_state":r["validation_state"],"rejection_reason":r["rejection_reason"],"created_at":r["created_at"],"decision_id":r["decision_id"],"decision_type":r["decision_type"],"selected_probability":r["selected_probability"],"decided_at":r["decided_at"],"promotion_action":r["promotion_action"],"evidence_id":r["promoted_evidence_id"],"promoted_at":r["promoted_at"]} for r in rows]
    return {"items":items,"total":int(total),"filtered_total":len(items)}


def get_candidate_assessment_recommendation(conn: sqlite3.Connection, candidate_id: int) -> dict[str, Any] | None:
    row = conn.execute("""
        SELECT * FROM candidate_assessment_recommendations
        WHERE candidate_id = ?
        ORDER BY created_at DESC, candidate_assessment_recommendation_id DESC
        LIMIT 1
    """, (candidate_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["category_transition"] = bool(result["category_transition"])
    return result
