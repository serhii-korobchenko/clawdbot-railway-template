#!/usr/bin/env python3
"""PROROK schema v18: append-only Evidence temporal provenance reviews."""
from __future__ import annotations
import argparse
import os
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE evidence_temporal_reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL CHECK(target_type IN ('evidence','candidate')),
    target_id INTEGER NOT NULL,
    baseline_assessment_id INTEGER NOT NULL REFERENCES assessments(assessment_id),
    decision TEXT NOT NULL CHECK(decision IN ('verified_new','already_incorporated')),
    rationale TEXT NOT NULL CHECK(length(trim(rationale)) >= 12),
    actor TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_evidence_temporal_reviews_latest
ON evidence_temporal_reviews(target_type,target_id,baseline_assessment_id,review_id DESC);
"""

def migrate(conn):
    current=conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if not current or str(current[0]) != "17":
        raise ValueError("expected schema_version=17")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_temporal_reviews'").fetchone():
        raise ValueError("temporal review table already exists")
    # sqlite3.executescript() commits any active transaction implicitly.
    # Execute DDL statements individually under a single explicit write lock.
    conn.execute("BEGIN IMMEDIATE")
    for statement in DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("foreign key validation failed")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db", default=os.getenv("PROROK_DB_PATH","/data/workspace/prorok/prorok.sqlite3"))
    p.add_argument("--check-only", action="store_true")
    args=p.parse_args()
    path=Path(args.db)
    if not path.exists():
        raise SystemExit("database does not exist")
    conn=sqlite3.connect(str(path))
    try:
        if args.check_only:
            v=conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            ok=v and str(v[0])=="18" and conn.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_temporal_reviews'").fetchone()
            print("OK" if ok else "MISSING")
            return 0 if ok else 1
        migrate(conn)
        conn.commit()
        print("schema v18 applied")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__=="__main__":
    raise SystemExit(main())
