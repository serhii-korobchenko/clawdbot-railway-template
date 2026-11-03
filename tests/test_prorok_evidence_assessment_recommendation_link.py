from __future__ import annotations
import sqlite3, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CLI=ROOT/"prorok"/"prorok_evidence_assessment_cli.py"

def make_db(path: Path):
    c=sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE runs(run_id INTEGER PRIMARY KEY,run_type TEXT,status TEXT,notes TEXT,finished_at TEXT,events_processed INTEGER);
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY,event_id TEXT,run_id INTEGER,assessed_at TEXT,probability_percent INTEGER,probability_band TEXT,probability_label TEXT,confidence TEXT,delta_from_previous INTEGER,rationale TEXT);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY,event_id TEXT,summary TEXT);
    CREATE TABLE evidence_assessment_recommendations(evidence_assessment_recommendation_id INTEGER PRIMARY KEY,event_id_snapshot TEXT,evidence_id INTEGER,baseline_assessment_id INTEGER,baseline_probability INTEGER,status TEXT);
    CREATE TABLE evidence_assessment_decisions(evidence_assessment_decision_id INTEGER PRIMARY KEY,event_id_snapshot TEXT NOT NULL,evidence_id_snapshot INTEGER NOT NULL,evidence_id INTEGER,baseline_assessment_id INTEGER,baseline_probability INTEGER,selected_probability INTEGER,assessment_id INTEGER,run_id INTEGER,decision_source TEXT,actor TEXT,decided_at TEXT,recommendation_id INTEGER);
    INSERT INTO assessments VALUES(12,'event_a',NULL,'2026-09-20T00:00:00Z',15,'10-20%','Ймовірність низька','medium',NULL,'baseline');
    INSERT INTO evidence_items VALUES(8,'event_a','Official evidence');
    INSERT INTO evidence_assessment_recommendations VALUES(31,'event_a',8,12,15,'ready');
    """)
    c.commit(); c.close()

def run_cli(db, probability=20, recommendation_id=31):
    args=[sys.executable,str(CLI),"--db",str(db),"event_a","8","--baseline-assessment-id","12","--probability",str(probability),"--source","telegram"]
    if recommendation_id is not None: args += ["--recommendation-id",str(recommendation_id)]
    return subprocess.run(args,capture_output=True,text=True)

def test_accept_recommendation_links_provenance(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    r=run_cli(db,20); assert r.returncode==0, r.stderr
    c=sqlite3.connect(db)
    row=c.execute("SELECT recommendation_id,selected_probability,evidence_id_snapshot,evidence_id FROM evidence_assessment_decisions").fetchone()
    assert row==(31,20,8,8); c.close()

def test_custom_probability_keeps_recommendation_link(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    r=run_cli(db,10); assert r.returncode==0, r.stderr
    c=sqlite3.connect(db); assert c.execute("SELECT recommendation_id,selected_probability FROM evidence_assessment_decisions").fetchone()==(31,10); c.close()

def test_keep_current_keeps_recommendation_link(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    r=run_cli(db,15); assert r.returncode==0, r.stderr
    c=sqlite3.connect(db); assert c.execute("SELECT recommendation_id,selected_probability FROM evidence_assessment_decisions").fetchone()==(31,15); c.close()

def test_manual_flow_remains_backward_compatible(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    r=run_cli(db,20,None); assert r.returncode==0, r.stderr
    c=sqlite3.connect(db); assert c.execute("SELECT recommendation_id FROM evidence_assessment_decisions").fetchone()[0] is None; c.close()

def test_wrong_recommendation_is_rejected_atomically(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    c=sqlite3.connect(db); c.execute("UPDATE evidence_assessment_recommendations SET evidence_id=9 WHERE evidence_assessment_recommendation_id=31"); c.commit(); c.close()
    r=run_cli(db,20); assert r.returncode==1; assert "does not belong" in r.stderr
    c=sqlite3.connect(db); assert c.execute("SELECT COUNT(*) FROM evidence_assessment_decisions").fetchone()[0]==0; assert c.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]==1; c.close()

def test_stale_baseline_is_rejected_atomically(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    c=sqlite3.connect(db); c.execute("INSERT INTO assessments VALUES(13,'event_a',NULL,'2026-09-21T00:00:00Z',25,'25-35%','Малоймовірно','medium',10,'new')"); c.commit(); c.close()
    r=run_cli(db,20); assert r.returncode==1; assert "stale baseline" in r.stderr
    c=sqlite3.connect(db); assert c.execute("SELECT COUNT(*) FROM evidence_assessment_decisions").fetchone()[0]==0; assert c.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]==2; c.close()

def test_recommendation_cannot_be_decided_twice(tmp_path):
    db=tmp_path/"p.sqlite3"; make_db(db)
    assert run_cli(db,20).returncode==0
    r=run_cli(db,20); assert r.returncode==1
    assert "stale baseline" in r.stderr or "already has a decision" in r.stderr
