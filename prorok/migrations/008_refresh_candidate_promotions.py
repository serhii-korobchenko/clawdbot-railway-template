#!/usr/bin/env python3
"""Migrate PROROK SQLite schema from v7 to v8 candidate promotion audit.

v8 adds the deterministic bridge between refresh candidate quarantine and
official evidence finalization:

- refresh_user_decisions.run_id links each new finalization decision to its run;
- refresh_candidate_promotions records candidate -> official evidence mappings;
- historical decisions are not promoted automatically;
- historical decision run_id is backfilled only when its assessment already
  points to a run.

The migration never changes official probabilities and never inserts official
evidence.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "8"

PROMOTION_ACTIONS = ("inserted", "reused")

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS refresh_candidate_promotions (
    promotion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL UNIQUE,
    refresh_event_result_id INTEGER NOT NULL,
    decision_id INTEGER NOT NULL,
    evidence_id INTEGER,
    run_id INTEGER NOT NULL,
    promotion_action TEXT NOT NULL
        CHECK(promotion_action IN ('inserted', 'reused')),
    promoted_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    FOREIGN KEY(candidate_id)
        REFERENCES refresh_candidate_evidence(candidate_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(refresh_event_result_id)
        REFERENCES refresh_event_results(refresh_event_result_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(decision_id)
        REFERENCES refresh_user_decisions(decision_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(evidence_id)
        REFERENCES evidence_items(evidence_id)
        ON DELETE SET NULL,
    FOREIGN KEY(run_id)
        REFERENCES runs(run_id)
        ON DELETE RESTRICT
);
"""

INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_refresh_user_decisions_run
ON refresh_user_decisions(run_id)
WHERE run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_refresh_candidate_promotions_decision
ON refresh_candidate_promotions(decision_id, promotion_id);

CREATE INDEX IF NOT EXISTS idx_refresh_candidate_promotions_result
ON refresh_candidate_promotions(refresh_event_result_id, promotion_id);

CREATE INDEX IF NOT EXISTS idx_refresh_candidate_promotions_evidence
ON refresh_candidate_promotions(evidence_id)
WHERE evidence_id IS NOT NULL;
"""


def resolve_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(
        os.getenv("PROROK_DB_PATH")
        or os.getenv("PROROK_DB")
        or DEFAULT_DB
    ).resolve()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {
        row["name"]: row
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return row["value"] if row else None


def apply_migration(conn: sqlite3.Connection) -> None:
    required = (
        "refresh_user_decisions",
        "refresh_candidate_evidence",
        "refresh_event_results",
        "evidence_items",
        "runs",
    )
    for table in required:
        if not table_exists(conn, table):
            raise RuntimeError(
                f"{table} is missing; apply migrations through v7 before migration 008"
            )

    decision_columns = table_columns(conn, "refresh_user_decisions")
    if "run_id" not in decision_columns:
        conn.execute(
            """
            ALTER TABLE refresh_user_decisions
            ADD COLUMN run_id INTEGER
                REFERENCES runs(run_id)
                ON DELETE SET NULL
            """
        )

    # Historical accept/custom decisions already link to an assessment. Preserve
    # that known run provenance without creating any new runs or evidence.
    conn.execute(
        """
        UPDATE refresh_user_decisions
        SET run_id = (
            SELECT a.run_id
            FROM assessments a
            WHERE a.assessment_id = refresh_user_decisions.assessment_id
        )
        WHERE run_id IS NULL
          AND assessment_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM assessments a
              WHERE a.assessment_id = refresh_user_decisions.assessment_id
                AND a.run_id IS NOT NULL
          )
        """
    )

    conn.execute(TABLE_SQL)
    conn.executescript(INDEX_SQL)

    conn.execute(
        """
        INSERT INTO meta(key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (TARGET_VERSION,),
    )


def validate(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []

    if not table_exists(conn, "refresh_user_decisions"):
        errors.append("missing table: refresh_user_decisions")
        return errors

    decision_columns = table_columns(conn, "refresh_user_decisions")
    if "run_id" not in decision_columns:
        errors.append("refresh_user_decisions missing column: run_id")

    if not table_exists(conn, "refresh_candidate_promotions"):
        errors.append("missing table: refresh_candidate_promotions")
        return errors

    required_promotion_columns = {
        "promotion_id",
        "candidate_id",
        "refresh_event_result_id",
        "decision_id",
        "evidence_id",
        "run_id",
        "promotion_action",
        "promoted_at",
    }
    promotion_columns = table_columns(conn, "refresh_candidate_promotions")
    for name in sorted(required_promotion_columns):
        if name not in promotion_columns:
            errors.append(f"refresh_candidate_promotions missing column: {name}")

    invalid_actions = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refresh_candidate_promotions
        WHERE promotion_action NOT IN ('inserted', 'reused')
        """
    ).fetchone()["n"]
    if invalid_actions:
        errors.append(f"invalid promotion_action rows: {invalid_actions}")

    duplicate_candidates = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM (
            SELECT candidate_id
            FROM refresh_candidate_promotions
            GROUP BY candidate_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()["n"]
    if duplicate_candidates:
        errors.append(f"duplicate promoted candidate groups: {duplicate_candidates}")

    mismatched_results = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refresh_candidate_promotions p
        JOIN refresh_candidate_evidence c
          ON c.candidate_id = p.candidate_id
        WHERE c.refresh_event_result_id <> p.refresh_event_result_id
        """
    ).fetchone()["n"]
    if mismatched_results:
        errors.append(
            f"candidate/result mismatch promotion rows: {mismatched_results}"
        )

    mismatched_decisions = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refresh_candidate_promotions p
        JOIN refresh_user_decisions d
          ON d.decision_id = p.decision_id
        WHERE d.refresh_event_result_id <> p.refresh_event_result_id
        """
    ).fetchone()["n"]
    if mismatched_decisions:
        errors.append(
            f"decision/result mismatch promotion rows: {mismatched_decisions}"
        )

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"foreign_key_check returned {len(fk_errors)} error(s)")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply PROROK schema migration v7 -> v8 candidate promotions"
    )
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate v8 state without changing the database",
    )
    args = parser.parse_args()

    db = resolve_db(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        before_version = schema_version(conn)

        if args.check_only:
            errors = validate(conn)
            print("PROROK migration v8 check")
            print(f"db: {db}")
            print(f"schema_version: {before_version}")
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            if before_version != TARGET_VERSION:
                print(
                    f"ERROR: schema_version expected {TARGET_VERSION}, got {before_version!r}"
                )
                return 1
            print("v8_schema: ok")
            return 0

        if before_version not in {"7", "8"}:
            raise RuntimeError(
                f"expected schema_version 7 or 8 before migration, got {before_version!r}"
            )

        conn.execute("BEGIN IMMEDIATE")
        apply_migration(conn)

        errors = validate(conn)
        if errors:
            raise RuntimeError("; ".join(errors))

        after_version = schema_version(conn)
        if after_version != TARGET_VERSION:
            raise RuntimeError(
                f"meta.schema_version expected {TARGET_VERSION}, got {after_version!r}"
            )

        conn.commit()

        print("OK: PROROK schema migration v8 applied")
        print(f"db: {db}")
        print(f"schema_version_before: {before_version}")
        print(f"schema_version_after: {after_version}")
        print("refresh_user_decisions.run_id: yes")
        print("refresh_candidate_promotions table: yes")
        print("historical official evidence promoted: no")
        print("foreign_key_check: ok")
        return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
