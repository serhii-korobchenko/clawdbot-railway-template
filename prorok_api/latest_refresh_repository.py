from __future__ import annotations

import sqlite3
from typing import Any


def _decision_from_row(row: sqlite3.Row) -> dict[str, Any] | None:
    if row["decision_id"] is None:
        return None
    return {
        "decision_id": row["decision_id"],
        "decision_type": row["decision_type"],
        "selected_probability": row["selected_probability"],
        "assessment_id": row["decision_assessment_id"],
        "decision_source": row["decision_source"],
        "decided_at": row["decided_at"],
    }


def get_latest_refresh(conn: sqlite3.Connection) -> dict[str, Any]:
    run = conn.execute(
        """
        SELECT
            refresh_id,
            mode,
            trigger_source,
            scope,
            target_event_id,
            started_at,
            finished_at,
            status,
            phase,
            events_checked,
            events_with_new_evidence,
            new_evidence_count,
            recommendations_count,
            no_change_count,
            error_count,
            collector_version
        FROM refresh_runs
        ORDER BY refresh_id DESC
        LIMIT 1
        """
    ).fetchone()

    if run is None:
        return {"refresh": None, "results": []}

    rows = conn.execute(
        """
        SELECT
            rer.refresh_event_result_id,
            rer.event_id,
            rer.event_title_snapshot,
            rer.job_state,
            rer.outcome,
            rer.baseline_probability,
            rer.new_evidence_count,
            rer.indicator_count,
            rer.counterindicator_count,
            rer.candidate_rejected_count,
            rer.recommendation_valid,
            rer.recommended_probability,
            rer.recommendation_confidence,
            rer.change_recommended,
            les.probability_percent AS current_probability,
            d.decision_id,
            d.decision_type,
            d.selected_probability,
            d.assessment_id AS decision_assessment_id,
            d.decision_source,
            d.decided_at
        FROM refresh_event_results rer
        LEFT JOIN latest_event_state les
          ON les.event_id = rer.event_id
        LEFT JOIN refresh_user_decisions d
          ON d.refresh_event_result_id = rer.refresh_event_result_id
        WHERE rer.refresh_id = ?
        ORDER BY rer.refresh_event_result_id ASC
        """,
        (run["refresh_id"],),
    ).fetchall()

    results = []
    for row in rows:
        results.append(
            {
                "refresh_event_result_id": row["refresh_event_result_id"],
                "event_id": row["event_id"],
                "event_title_snapshot": row["event_title_snapshot"],
                "job_state": row["job_state"],
                "outcome": row["outcome"],
                "baseline_probability": row["baseline_probability"],
                "new_evidence_count": int(row["new_evidence_count"] or 0),
                "indicator_count": int(row["indicator_count"] or 0),
                "counterindicator_count": int(row["counterindicator_count"] or 0),
                "candidate_rejected_count": int(row["candidate_rejected_count"] or 0),
                "recommendation_valid": bool(row["recommendation_valid"]),
                "recommended_probability": row["recommended_probability"],
                "recommendation_confidence": row["recommendation_confidence"],
                "change_recommended": bool(row["change_recommended"]),
                "current_probability": row["current_probability"],
                "decision": _decision_from_row(row),
            }
        )

    return {
        "refresh": {
            "refresh_id": run["refresh_id"],
            "mode": run["mode"],
            "trigger_source": run["trigger_source"],
            "scope": run["scope"],
            "target_event_id": run["target_event_id"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "status": run["status"],
            "phase": run["phase"],
            "events_checked": int(run["events_checked"] or 0),
            "events_with_new_evidence": int(run["events_with_new_evidence"] or 0),
            "new_evidence_count": int(run["new_evidence_count"] or 0),
            "recommendations_count": int(run["recommendations_count"] or 0),
            "no_change_count": int(run["no_change_count"] or 0),
            "error_count": int(run["error_count"] or 0),
            "collector_version": run["collector_version"],
        },
        "results": results,
    }
