#!/usr/bin/env python3
"""PROROK refresh collector v9 with late-success recovery reconciliation.

v9 preserves the universal v8 deterministic search-quality gate and adds a
recovery pass for rows previously finalized as execution_failed.

Why:
OpenClaw cron may record a transient failed attempt (for example a TPM rate
limit) and later record a newer successful retry for the same cron_id. Older
collectors could terminalize the PROROK result on the first failure and never
revisit it because pending_rows() only returns pending/scheduled rows.

Recovery rule:
- normal pending/scheduled collection remains unchanged and uses v8;
- execution_failed rows are inspected read-only against their cron run history;
- only when the latest cron run is newer than the run already recorded on the
  PROROK result AND has status ok/success/completed is the row reprocessed;
- reprocessing uses the full v8 deterministic gate before any candidate rows
  can be persisted;
- if no newer successful cron run exists, the terminal failure is untouched.

No schema migration is required.

Safety invariants:
- official events, assessments, evidence_items, and sources are never written;
- recovered candidate-positive runs still pass the universal v8 gate first;
- existing execution_failed rows are not reopened merely because time passed;
  recovery requires a concretely newer successful cron run.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import prorok_refresh_collector_v8 as v8

COLLECTOR_VERSION = "9"
SUCCESS_STATUSES = {"ok", "success", "completed"}

base = v8.v7.v6.v5.v4.base
_ORIGINAL_COLLECT_ONCE = base.collect_once


def _recovery_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return execution_failed rows eligible for late-success inspection."""
    return conn.execute(
        """
        SELECT *
        FROM refresh_event_results
        WHERE job_state = 'execution_failed'
          AND cron_id IS NOT NULL
          AND cron_run_at_ms IS NOT NULL
        ORDER BY refresh_event_result_id
        """
    ).fetchall()


def recover_late_successes(
    db: Path,
    state_dir: Path,
) -> dict[str, int]:
    """Reprocess execution_failed rows only when a newer successful cron run exists."""
    counts: dict[str, int] = {}
    conn = base.connect_db(db)

    try:
        rows = _recovery_rows(conn)

        for row in rows:
            cron_id = row["cron_id"]
            if not cron_id:
                continue

            latest = base.find_latest_finished_run(state_dir, cron_id)
            if latest is None:
                continue

            recorded_run_at_ms = int(row["cron_run_at_ms"] or 0)
            if latest.run_at_ms <= recorded_run_at_ms:
                continue

            if latest.status not in SUCCESS_STATUSES:
                continue

            result = v8.collect_one_v8(conn, state_dir, row)
            key = f"recovery:{result}"
            counts[key] = counts.get(key, 0) + 1

        return counts
    finally:
        conn.close()


def collect_once_v9(
    db: Path,
    state_dir: Path,
    limit: int = 100,
) -> dict[str, int]:
    """Run normal v8 collection, then reconcile late successful cron retries."""
    counts = dict(_ORIGINAL_COLLECT_ONCE(db, state_dir, limit))

    recovered = recover_late_successes(db, state_dir)
    for key, value in recovered.items():
        counts[key] = counts.get(key, 0) + value

    return counts


def main(argv: list[str] | None = None) -> int:
    base.COLLECTOR_VERSION = COLLECTOR_VERSION
    base.collect_one = v8.collect_one_v8
    base.collect_once = collect_once_v9
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
