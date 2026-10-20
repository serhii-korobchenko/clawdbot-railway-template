#!/usr/bin/env python3
"""PROROK schema v15: preserve recommendation/decision provenance after evidence hard-delete."""
from __future__ import annotations
import argparse, os, sqlite3, sys
from pathlib import Path

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION = "15"

RECOMMENDATIONS_TABLE = """
CREATE TABLE evidence_assessment_recommendations_v15 (
    evidence_assessment_recommendation_id INTEGER PRIMARY KEY,
    event_id_snapshot TEXT NOT NULL,
    evidence_id_snapshot INTEGER NOT NULL,
    evidence_id INTEGER,
    baseline_assessment_id INTEGER NOT NULL,
    baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),
    recommended_probability INTEGER NOT NULL CHECK(recommended_probability BETWEEN 0 AND 100 AND recommended_probability % 5 = 0),
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
    FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id) ON DELETE SET NULL,
    FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
)
"""

DECISIONS_TABLE = """
CREATE TABLE evidence_assessment_decisions_v15 (
    evidence_assessment_decision_id INTEGER PRIMARY KEY,
    event_id_snapshot TEXT NOT NULL,
    evidence_id_snapshot INTEGER NOT NULL,
    evidence_id INTEGER,
    baseline_assessment_id INTEGER NOT NULL,
    baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),
    selected_probability INTEGER NOT NULL CHECK(selected_probability BETWEEN 0 AND 100),
    assessment_id INTEGER NOT NULL UNIQUE,
    run_id INTEGER NOT NULL,
    decision_source TEXT NOT NULL DEFAULT 'telegram',
    actor TEXT,
    decided_at TEXT NOT NULL,
    recommendation_id INTEGER REFERENCES evidence_assessment_recommendations(evidence_assessment_recommendation_id),
    FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id) ON DELETE SET NULL,
    FOREIGN KEY(assessment_id) REFERENCES assessments(assessment_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
)
"""

RECOMMENDATION_COLUMNS = (
    "evidence_assessment_recommendation_id,event_id_snapshot,evidence_id_snapshot,evidence_id,"
    "baseline_assessment_id,baseline_probability,recommended_probability,probability_delta,"
    "recommended_band,recommended_label,recommendation_confidence,change_from_baseline,"
    "net_evidence_direction,net_evidence_impact,baseline_incorporation,category_transition,"
    "recommendation_rationale,delta_justification,methodology_version,parser_version,agent_id,"
    "model_used,run_id,source_run_key,created_at,status"
)
DECISION_COLUMNS = (
    "evidence_assessment_decision_id,event_id_snapshot,evidence_id_snapshot,evidence_id,"
    "baseline_assessment_id,baseline_probability,selected_probability,assessment_id,run_id,"
    "decision_source,actor,decided_at,recommendation_id"
)


def resolve_db(explicit):
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()

def version(c):
    r=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone(); return None if r is None else str(r[0])

def table_exists(c,n):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(n,)).fetchone() is not None

def columns(c,n):
    return {r[1] for r in c.execute(f"PRAGMA table_info({n})")}

def fk_rows(c,n):
    return c.execute(f"PRAGMA foreign_key_list({n})").fetchall()

def evidence_fk_is_set_null(c,n):
    return any(r[2]=="evidence_items" and r[3]=="evidence_id" and r[4]=="evidence_id" and str(r[6]).upper()=="SET NULL" for r in fk_rows(c,n))

def validate(c):
    errors=[]
    for table in ("evidence_assessment_recommendations","evidence_assessment_decisions"):
        if not table_exists(c,table): errors.append(f"missing table: {table}"); continue
        cols=columns(c,table)
        if "evidence_id_snapshot" not in cols: errors.append(f"{table} missing column: evidence_id_snapshot")
        info={r[1]:r for r in c.execute(f"PRAGMA table_info({table})")}
        if "evidence_id" not in info or info["evidence_id"][3] != 0: errors.append(f"{table}.evidence_id must be nullable")
        if not evidence_fk_is_set_null(c,table): errors.append(f"{table}.evidence_id FK must use ON DELETE SET NULL")
    if c.execute("PRAGMA foreign_key_check").fetchall(): errors.append("foreign_key_check failed")
    return errors

def apply_migration(c):
    rec_count=c.execute("SELECT COUNT(*) FROM evidence_assessment_recommendations").fetchone()[0]
    rec_ids=[r[0] for r in c.execute("SELECT evidence_assessment_recommendation_id FROM evidence_assessment_recommendations ORDER BY 1")]
    dec_count=c.execute("SELECT COUNT(*) FROM evidence_assessment_decisions").fetchone()[0]
    dec_ids=[r[0] for r in c.execute("SELECT evidence_assessment_decision_id FROM evidence_assessment_decisions ORDER BY 1")]
    dec_links=[tuple(r) for r in c.execute("SELECT evidence_assessment_decision_id,recommendation_id FROM evidence_assessment_decisions ORDER BY 1")]

    c.execute("DROP TABLE IF EXISTS evidence_assessment_decisions_v15")
    c.execute("DROP TABLE IF EXISTS evidence_assessment_recommendations_v15")
    c.execute(RECOMMENDATIONS_TABLE)
    c.execute(f"INSERT INTO evidence_assessment_recommendations_v15({RECOMMENDATION_COLUMNS}) SELECT evidence_assessment_recommendation_id,event_id_snapshot,evidence_id,evidence_id,baseline_assessment_id,baseline_probability,recommended_probability,probability_delta,recommended_band,recommended_label,recommendation_confidence,change_from_baseline,net_evidence_direction,net_evidence_impact,baseline_incorporation,category_transition,recommendation_rationale,delta_justification,methodology_version,parser_version,agent_id,model_used,run_id,source_run_key,created_at,status FROM evidence_assessment_recommendations")
    c.execute(DECISIONS_TABLE)
    c.execute(f"INSERT INTO evidence_assessment_decisions_v15({DECISION_COLUMNS}) SELECT evidence_assessment_decision_id,event_id_snapshot,evidence_id,evidence_id,baseline_assessment_id,baseline_probability,selected_probability,assessment_id,run_id,decision_source,actor,decided_at,recommendation_id FROM evidence_assessment_decisions")
    c.execute("DROP TABLE evidence_assessment_decisions")
    c.execute("DROP TABLE evidence_assessment_recommendations")
    c.execute("ALTER TABLE evidence_assessment_recommendations_v15 RENAME TO evidence_assessment_recommendations")
    c.execute("ALTER TABLE evidence_assessment_decisions_v15 RENAME TO evidence_assessment_decisions")
    c.executescript("""
    CREATE INDEX IF NOT EXISTS idx_evidence_assessment_recommendations_evidence ON evidence_assessment_recommendations(evidence_id,created_at DESC,evidence_assessment_recommendation_id DESC);
    CREATE INDEX IF NOT EXISTS idx_evidence_assessment_recommendations_event ON evidence_assessment_recommendations(event_id_snapshot,created_at DESC,evidence_assessment_recommendation_id DESC);
    CREATE INDEX IF NOT EXISTS idx_evidence_assessment_decisions_recommendation ON evidence_assessment_decisions(recommendation_id);
    """)
    if c.execute("SELECT COUNT(*) FROM evidence_assessment_recommendations").fetchone()[0] != rec_count: raise RuntimeError("recommendation row count changed")
    if [r[0] for r in c.execute("SELECT evidence_assessment_recommendation_id FROM evidence_assessment_recommendations ORDER BY 1")] != rec_ids: raise RuntimeError("recommendation ids changed")
    if c.execute("SELECT COUNT(*) FROM evidence_assessment_decisions").fetchone()[0] != dec_count: raise RuntimeError("decision row count changed")
    if [r[0] for r in c.execute("SELECT evidence_assessment_decision_id FROM evidence_assessment_decisions ORDER BY 1")] != dec_ids: raise RuntimeError("decision ids changed")
    if [tuple(r) for r in c.execute("SELECT evidence_assessment_decision_id,recommendation_id FROM evidence_assessment_decisions ORDER BY 1")] != dec_links: raise RuntimeError("decision recommendation links changed")
    c.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",(TARGET_VERSION,))

def main():
    p=argparse.ArgumentParser(description="Apply PROROK schema migration v14 -> v15 audit-safe evidence deletion"); p.add_argument("--db"); p.add_argument("--check-only",action="store_true"); a=p.parse_args()
    db=resolve_db(a.db)
    if not db.exists(): print(f"ERROR: DB not found: {db}",file=sys.stderr); return 1
    c=sqlite3.connect(str(db),timeout=30)
    try:
        c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000"); before=version(c)
        if a.check_only:
            errors=validate(c)
            if before!=TARGET_VERSION: errors.append(f"schema_version expected 15, got {before!r}")
            if errors:
                for e in errors: print(f"ERROR: {e}")
                return 1
            print("OK: PROROK schema v15 verified"); return 0
        if before!="14": raise RuntimeError(f"schema_version expected 14 before migration, got {before!r}")
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("BEGIN IMMEDIATE")
        apply_migration(c)
        fk_errors=c.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors: raise RuntimeError(f"foreign_key_check failed: {fk_errors[:5]}")
        errors=validate(c)
        if errors: raise RuntimeError("; ".join(errors))
        c.commit(); c.execute("PRAGMA foreign_keys=ON")
        print("OK: PROROK schema migration v15 applied")
        print(f"schema_version_before: {before}"); print("schema_version_after: 15")
        print("recommendation evidence snapshot: yes"); print("decision evidence snapshot: yes")
        print("live evidence FK: nullable ON DELETE SET NULL"); print("recommendation/decision ids and links preserved: yes"); print("foreign_key_check: ok")
        return 0
    except Exception as e:
        c.rollback(); print(f"ERROR: {e}",file=sys.stderr); return 1
    finally: c.close()
if __name__=="__main__": raise SystemExit(main())
