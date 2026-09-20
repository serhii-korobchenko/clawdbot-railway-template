#!/usr/bin/env python3
"""PROROK schema v12: evidence-based manual assessment provenance.

Adds an append-only link from one official evidence item to one user-created
assessment decision. Existing refresh decisions, assessments, evidence and
probabilities are not changed.
"""
from __future__ import annotations
import argparse, os, sqlite3, sys
from pathlib import Path

DEFAULT_DB="/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION="12"

DDL="""
CREATE TABLE IF NOT EXISTS evidence_assessment_decisions (
    evidence_assessment_decision_id INTEGER PRIMARY KEY,
    event_id_snapshot TEXT NOT NULL,
    evidence_id INTEGER NOT NULL,
    baseline_assessment_id INTEGER NOT NULL,
    baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),
    selected_probability INTEGER NOT NULL CHECK(selected_probability BETWEEN 0 AND 100),
    assessment_id INTEGER NOT NULL UNIQUE,
    run_id INTEGER NOT NULL,
    decision_source TEXT NOT NULL DEFAULT 'telegram',
    actor TEXT,
    decided_at TEXT NOT NULL,
    FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id),
    FOREIGN KEY(assessment_id) REFERENCES assessments(assessment_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_assessment_decisions_evidence
ON evidence_assessment_decisions(evidence_id, decided_at DESC, evidence_assessment_decision_id DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_assessment_decisions_event
ON evidence_assessment_decisions(event_id_snapshot, decided_at DESC, evidence_assessment_decision_id DESC);
"""

def resolve_db(explicit):
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()
def table_exists(c,n):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(n,)).fetchone() is not None
def version(c):
    r=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return None if r is None else str(r[0])
def validate(c):
    errors=[]
    if not table_exists(c,"evidence_assessment_decisions"): return ["missing table: evidence_assessment_decisions"]
    cols={r[1] for r in c.execute("PRAGMA table_info(evidence_assessment_decisions)")}
    required={"evidence_assessment_decision_id","event_id_snapshot","evidence_id","baseline_assessment_id","baseline_probability","selected_probability","assessment_id","run_id","decision_source","actor","decided_at"}
    errors += [f"evidence_assessment_decisions missing column: {x}" for x in sorted(required-cols)]
    if c.execute("PRAGMA foreign_key_check").fetchall(): errors.append("foreign_key_check failed")
    return errors
def main():
    p=argparse.ArgumentParser(description="Apply PROROK schema migration v11 -> v12 evidence assessment provenance")
    p.add_argument("--db"); p.add_argument("--check-only",action="store_true"); a=p.parse_args()
    db=resolve_db(a.db)
    if not db.exists(): print(f"ERROR: DB not found: {db}",file=sys.stderr); return 1
    c=sqlite3.connect(str(db),timeout=30)
    try:
        c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000"); before=version(c)
        if a.check_only:
            errors=validate(c)
            if before!=TARGET_VERSION: errors.append(f"schema_version expected 12, got {before!r}")
            if errors:
                for e in errors: print(f"ERROR: {e}")
                return 1
            print("PROROK migration v12 check: ok"); return 0
        if before not in {"11","12"}: raise RuntimeError(f"expected schema_version 11 or 12 before migration, got {before!r}")
        c.execute("BEGIN IMMEDIATE"); c.executescript(DDL)
        c.execute("""INSERT INTO meta(key,value) VALUES('schema_version',?)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",(TARGET_VERSION,))
        errors=validate(c)
        if errors: raise RuntimeError("; ".join(errors))
        c.commit()
        print("OK: PROROK schema migration v12 applied"); print(f"schema_version_before: {before}"); print("schema_version_after: 12")
        print("evidence_assessment_decisions table: yes"); print("existing assessments/evidence changed: no"); print("foreign_key_check: ok")
        return 0
    except Exception as e:
        c.rollback(); print(f"ERROR: {e}",file=sys.stderr); return 1
    finally: c.close()
if __name__=="__main__": raise SystemExit(main())
