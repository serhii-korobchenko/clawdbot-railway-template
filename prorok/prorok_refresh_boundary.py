#!/usr/bin/env python3
"""Shared deterministic search-boundary resolution for PROROK refreshes.

A refresh run start is safe to advance the search boundary when either:
- the refresh has been explicitly finalized by a user decision; or
- it completed with no new evidence and passed the deterministic search-quality gate.

Undecided refreshes that contain new evidence never advance the boundary.
Callers retain their existing official-assessment fallback when no safe refresh exists.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    if not _table_exists(conn, name):
        return set()
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({name})")
    }


def latest_safe_refresh_boundary_ms(
    conn: sqlite3.Connection,
    event_id: str,
) -> int | None:
    """Return the latest safe refresh cron start for an event, in epoch ms."""
    result_columns = _table_columns(conn, "refresh_event_results")
    required_result_columns = {
        "refresh_event_result_id",
        "event_id",
        "job_state",
        "cron_status",
        "cron_run_at_ms",
    }
    if not required_result_columns.issubset(result_columns):
        return None

    candidates: list[int] = []

    decision_columns = _table_columns(conn, "refresh_user_decisions")
    required_decision_columns = {
        "refresh_event_result_id",
        "event_id_snapshot",
    }
    if required_decision_columns.issubset(decision_columns):
        finalized = conn.execute(
            """
            SELECT MAX(rer.cron_run_at_ms) AS boundary_ms
            FROM refresh_user_decisions rud
            JOIN refresh_event_results rer
              ON rer.refresh_event_result_id = rud.refresh_event_result_id
            WHERE rud.event_id_snapshot = ?
              AND rer.event_id = ?
              AND rer.job_state = 'completed'
              AND rer.cron_status = 'ok'
              AND rer.cron_run_at_ms IS NOT NULL
            """,
            (event_id, event_id),
        ).fetchone()
        if finalized is not None and finalized["boundary_ms"] is not None:
            candidates.append(int(finalized["boundary_ms"]))

    no_new_required = {
        "outcome",
        "search_quality_valid",
    }
    if no_new_required.issubset(result_columns):
        no_new = conn.execute(
            """
            SELECT MAX(cron_run_at_ms) AS boundary_ms
            FROM refresh_event_results
            WHERE event_id = ?
              AND job_state = 'completed'
              AND cron_status = 'ok'
              AND cron_run_at_ms IS NOT NULL
              AND outcome = 'no_new_evidence'
              AND search_quality_valid = 1
            """,
            (event_id,),
        ).fetchone()
        if no_new is not None and no_new["boundary_ms"] is not None:
            candidates.append(int(no_new["boundary_ms"]))

    return max(candidates) if candidates else None


def latest_safe_refresh_boundary_datetime(
    conn: sqlite3.Connection,
    event_id: str,
) -> datetime | None:
    """Return the latest safe refresh cron start as an aware UTC datetime."""
    boundary_ms = latest_safe_refresh_boundary_ms(conn, event_id)
    if boundary_ms is None:
        return None
    return datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc)
