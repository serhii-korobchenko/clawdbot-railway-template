#!/usr/bin/env python3
"""Conservative PROROK temporal provenance gate.

Publication time is not fact time. Recent publication is a chronological signal,
not proof that content is novel. Older or missing dates require an explicit,
auditable review before a new assessment/recommendation may be generated.
"""
from __future__ import annotations

from datetime import datetime, date, time, timezone
import sqlite3


def _parse_timestamp(value):
    if value is None or not str(value).strip():
        return None, False
    raw = str(value).strip()
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.min, tzinfo=timezone.utc), True
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Naive timestamps cannot be ordered safely against UTC assessments.
            return None, False
        return parsed.astimezone(timezone.utc), False
    except ValueError:
        return None, False


def classify_publication(published_at, assessed_at):
    publication, day_precision = _parse_timestamp(published_at)
    assessment, _ = _parse_timestamp(assessed_at)
    if publication is None or assessment is None:
        return "unknown_date"
    if day_precision:
        # Full publication day must be strictly later than the assessment.
        next_day = publication.replace(hour=0, minute=0, second=0, microsecond=0)
        if next_day > assessment:
            return "newer_publication"
        return "review_required"
    return "newer_publication" if publication > assessment else "review_required"


def _table_exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None


def latest_assessment(conn, event_id):
    return conn.execute("""SELECT assessment_id,assessed_at FROM assessments
        WHERE event_id=? ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1""",(event_id,)).fetchone()


def latest_review(conn, kind, item_id, baseline_id):
    if not _table_exists(conn, "evidence_temporal_reviews"):
        return None
    return conn.execute("""SELECT review_id,decision,rationale,actor,reviewed_at FROM evidence_temporal_reviews
        WHERE target_type=? AND target_id=? AND baseline_assessment_id=?
        ORDER BY review_id DESC LIMIT 1""",(kind,int(item_id),int(baseline_id))).fetchone()


def _already_assessed(conn, kind, item_id):
    if kind != "evidence":
        return False
    # An official Evidence item previously used in a finalized assessment
    # cannot be priced into the forecast again merely due to a later arrival.
    if _table_exists(conn, "evidence_assessment_decisions"):
        exists=conn.execute(
            "SELECT 1 FROM evidence_assessment_decisions WHERE evidence_id=? LIMIT 1",
            (int(item_id),)).fetchone()
        if exists is not None:
            return True
    if _table_exists(conn, "refresh_candidate_promotions") and _table_exists(conn, "refresh_user_decisions"):
        columns={str(r[1]) for r in conn.execute("PRAGMA table_info(refresh_candidate_promotions)")}
        if "decision_id" in columns:
            exists=conn.execute("""
                SELECT 1 FROM refresh_candidate_promotions p
                JOIN refresh_user_decisions ud ON ud.decision_id=p.decision_id
                WHERE p.evidence_id=? AND ud.assessment_id IS NOT NULL LIMIT 1
            """,(int(item_id),)).fetchone()
            if exists is not None:
                return True
    return False


def inspect(conn, kind, item_id, *, event_id=None):
    if kind not in ("evidence","candidate"):
        raise ValueError("invalid temporal target")
    if kind == "evidence":
        row = conn.execute("""SELECT ei.evidence_id AS target_id, ei.event_id, s.published_at,
            ei.created_at AS ingested_at
            FROM evidence_items ei JOIN sources s ON s.source_id=ei.source_id
            WHERE ei.evidence_id=?""",(int(item_id),)).fetchone()
    else:
        row = conn.execute("""SELECT ce.candidate_id AS target_id, rr.event_id,
            ce.published_at, ce.created_at AS ingested_at
            FROM refresh_candidate_evidence ce JOIN refresh_event_results rr
            ON rr.refresh_event_result_id=ce.refresh_event_result_id
            WHERE ce.candidate_id=?""",(int(item_id),)).fetchone()
    if row is None:
        raise ValueError("temporal target not found")
    event = str(row["event_id"] or "")
    if not event or (event_id is not None and event != str(event_id)):
        raise ValueError("temporal target event mismatch")
    baseline = latest_assessment(conn,event)
    if baseline is None:
        raise ValueError("no current assessment")
    baseline_id=int(baseline["assessment_id"])
    classification=classify_publication(row["published_at"], baseline["assessed_at"])
    review=latest_review(conn,kind,item_id,baseline_id)
    decision=review["decision"] if review is not None else None
    if _already_assessed(conn, kind, item_id) or decision == "already_incorporated":
        status = "already_incorporated"
        allowed = False
    elif classification == "newer_publication":
        status = "newer_publication"
        allowed = True
    elif decision == "verified_new":
        status = "verified_new"
        allowed = True
    elif decision == "already_incorporated":
        status = "already_incorporated"
        allowed = False
    else:
        status = classification
        allowed = False
    return {
        "target_type":kind, "target_id":int(item_id), "event_id":event,
        "published_at":row["published_at"],"ingested_at":row["ingested_at"],
        "latest_assessed_at":baseline["assessed_at"],
        "latest_assessment_id":baseline_id,
        "status":status,"automated_reassessment_allowed":allowed,
        "review_id":review["review_id"] if review is not None else None,
        "review_decision":decision,"review_rationale":review["rationale"] if review is not None else None,
    }


def require_allowed(conn, kind, item_id, *, event_id=None):
    state=inspect(conn,kind,item_id,event_id=event_id)
    if not state["automated_reassessment_allowed"]:
        raise ValueError(f"temporal provenance blocked: {kind} #{item_id}: {state['status']}; "
                         "requires documented review against current assessment")
    return state


def enforce_if_enabled(conn, kind, item_id, *, event_id=None):
    """Protect v18+ databases while allowing pre-migration legacy test schemas.

    New Candidate Force runs must additionally require v18 explicitly; this
    compatibility wrapper is for existing individual Evidence/assessment flows.
    """
    if not _table_exists(conn, "evidence_temporal_reviews"):
        return None
    return require_allowed(conn,kind,item_id,event_id=event_id)
