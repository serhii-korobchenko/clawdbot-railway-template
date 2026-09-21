#!/usr/bin/env python3
"""PROROK schema v13: selected official evidence recommendation provenance."""

from __future__ import annotations
import argparse, os, sqlite3, sys
from pathlib import Path

DEFAULT_DB="/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION="13"

RECOMMENDATIONS_DDL="""
CREATE TABLE IF NOT EXISTS evidence_assessment_recommendations (
    evidence_assessment_recommendation_id INTEGER PRIMARY KEY,
    event_id_snapshot TEXT NOT NULL,
    evidence_id INTEGER NOT NULL,
    baseline_assessment_id INTEGER NOT NULL,
    baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),
    recommended_probability INTEGER NOT NULL CHECK(
        recommended_probability BETWEEN 0 AND 100 AND recommended_probability % 5 = 0
    ),
    probability_delta INTEGER NOT NULL,
    recommended_band TEXT NOT NULL,
    recommended_label TEXT NOT NULL,
    recommendation_confidence TEXT NOT NULL CHECK(recommendation_confidence IN ('low','medium','high')),
    change_from_baseline TEXT NOT NULL CHECK(change_from_baseline IN ('increase','decrease','no_update')),
    net_evidence_direction TEXT NOT NULL CHECK(net_evidence_direction IN ('positive','negative','balanced')),
    net_evidence_impact TEXT NOT NULL CHECK(net_evidence_impact IN ('none','weak','moderate','strong')),
    baseline_incorporation TEXT NOT NULL CHECK(baseline_incorporation IN ('low','medium','high')),
    category_transition INTEGER NOT NULL CHECK(category_transition IN (0,1)),
    recommendation_rationale TEXT NOT NULL,
    delta_justification TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    agent_id TEXT,
    model_used TEXT,
    run_id INTEGER,
    source_run_key TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','stale','error')),
    FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id),
    FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_assessment_recommendations_evidence
ON evidence_assessment_recommendations(evidence_id, created_at DESC, evidence_assessment_recommendation_id DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_assessment_recommendations_event
ON evidence_assessment_recommendations(event_id_snapshot, created_at DESC, evidence_assessment_recommendation_id DESC);
"""

def resolve_db(explicit):
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()

def version(c):
    r=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return None if r is None else str(r[0])

def table_exists(c,n):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(n,)).fetchone() is not None

def columns(c,n):
    return {r[1] for r in c.execute(f"PRAGMA table_info({n})")}

def validate(c):
    errors=[]
    if not table_exists(c,"evidence_assessment_recommendations"):
        errors.append("missing table: evidence_assessment_recommendations")
    else:
        required={"evidence_assessment_recommendation_id","event_id_snapshot","evidence_id",
          "baseline_assessment_id","baseline_probability","recommended_probability","probability_delta",
          "recommended_band","recommended_label","recommendation_confidence","change_from_baseline",
          "net_evidence_direction","net_evidence_impact","baseline_incorporation","category_transition",
          "recommendation_rationale","delta_justification","methodology_version","parser_version",
          "agent_id","model_used","run_id","source_run_key","created_at","status"}
        errors += [f"evidence_assessment_recommendations missing column: {x}" for x in sorted(required-columns(c,"evidence_assessment_recommendations"))]
    if not table_exists(c,"evidence_assessment_decisions"):
        errors.append("missing table: evidence_assessment_decisions")
    elif "recommendation_id" not in columns(c,"evidence_assessment_decisions"):
        errors.append("evidence_assessment_decisions missing column: recommendation_id")
    if c.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check failed")
    return errors

def main():
    p=argparse.ArgumentParser(description="Apply PROROK schema migration v12 -> v13 selected-evidence recommendations")
    p.add_argument("--db"); p.add_argument("--check-only",action="store_true"); a=p.parse_args()
    db=resolve_db(a.db)
    if not db.exists(): print(f"ERROR: DB not found: {db}",file=sys.stderr); return 1
    c=sqlite3.connect(str(db),timeout=30)
    try:
        c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000"); before=version(c)
        if a.check_only:
            errors=validate(c)
            if before!=TARGET_VERSION: errors.append(f"schema_version expected 13, got {before!r}")
            if errors:
                for e in errors: print(f"ERROR: {e}")
                return 1
            print("OK: PROROK schema v13 verified"); return 0
        if before!= "12":
            raise RuntimeError(f"schema_version expected 12 before migration, got {before!r}")
        c.execute("BEGIN IMMEDIATE")
        c.executescript(RECOMMENDATIONS_DDL)
        if "recommendation_id" not in columns(c,"evidence_assessment_decisions"):
            c.execute("ALTER TABLE evidence_assessment_decisions ADD COLUMN recommendation_id INTEGER REFERENCES evidence_assessment_recommendations(evidence_assessment_recommendation_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_evidence_assessment_decisions_recommendation ON evidence_assessment_decisions(recommendation_id)")
        c.execute("""INSERT INTO meta(key,value) VALUES('schema_version',?)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",(TARGET_VERSION,))
        errors=validate(c)
        if errors: raise RuntimeError("; ".join(errors))
        c.commit()
        print("OK: PROROK schema migration v13 applied")
        print(f"schema_version_before: {before}"); print("schema_version_after: 13")
        print("evidence_assessment_recommendations table: yes")
        print("evidence_assessment_decisions.recommendation_id: yes")
        print("existing assessments/evidence/decisions changed: no"); print("foreign_key_check: ok")
        return 0
    except Exception as e:
        c.rollback(); print(f"ERROR: {e}",file=sys.stderr); return 1
    finally: c.close()

if __name__=="__main__": raise SystemExit(main())
