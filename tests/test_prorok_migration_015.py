from __future__ import annotations
import importlib.util, sqlite3, subprocess, sys
from argparse import Namespace
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/"prorok"/"migrations"/"015_evidence_delete_provenance.py"
DELETE_CLI=ROOT/"prorok"/"prorok_delete_cli.py"

def load_delete_cli():
    spec=importlib.util.spec_from_file_location("prorok_delete_cli_v15",DELETE_CLI)
    m=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(m); return m

def make_v14_db(path: Path):
    c=sqlite3.connect(path); c.execute("PRAGMA foreign_keys=ON")
    c.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT);
    INSERT INTO meta(key,value) VALUES('schema_version','14');
    CREATE TABLE events(event_id TEXT PRIMARY KEY,title TEXT);
    CREATE TABLE runs(run_id INTEGER PRIMARY KEY);
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,run_id INTEGER,probability_percent INTEGER,FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL);
    CREATE TABLE sources(source_id INTEGER PRIMARY KEY,title TEXT);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,source_id INTEGER NOT NULL,run_id INTEGER,direction TEXT,strength TEXT,summary TEXT,FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE,FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL);
    CREATE TABLE deletion_audit(deletion_id INTEGER PRIMARY KEY AUTOINCREMENT,target_type TEXT NOT NULL,target_id_snapshot TEXT NOT NULL,event_id_snapshot TEXT,target_label_snapshot TEXT,source_id_snapshot INTEGER,assessment_count INTEGER NOT NULL DEFAULT 0,evidence_count INTEGER NOT NULL DEFAULT 0,decision_source TEXT NOT NULL DEFAULT 'telegram',actor_snapshot TEXT,deleted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    CREATE TABLE evidence_assessment_recommendations(
      evidence_assessment_recommendation_id INTEGER PRIMARY KEY,event_id_snapshot TEXT NOT NULL,evidence_id INTEGER NOT NULL,baseline_assessment_id INTEGER NOT NULL,baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),recommended_probability INTEGER NOT NULL CHECK(recommended_probability BETWEEN 0 AND 100 AND recommended_probability % 5=0),probability_delta INTEGER NOT NULL,recommended_band TEXT NOT NULL,recommended_label TEXT NOT NULL,recommendation_confidence TEXT NOT NULL CHECK(recommendation_confidence IN ('low','medium','high')),change_from_baseline TEXT NOT NULL CHECK(change_from_baseline IN ('increase','decrease','no_update')),net_evidence_direction TEXT NOT NULL CHECK(net_evidence_direction IN ('positive','negative','balanced')),net_evidence_impact TEXT NOT NULL CHECK(net_evidence_impact IN ('none','weak','moderate','strong')),baseline_incorporation TEXT NOT NULL CHECK(baseline_incorporation IN ('low','medium','high')),category_transition INTEGER NOT NULL CHECK(category_transition IN (0,1)),recommendation_rationale TEXT NOT NULL,delta_justification TEXT NOT NULL,methodology_version TEXT NOT NULL,parser_version TEXT NOT NULL,agent_id TEXT,model_used TEXT,run_id INTEGER,source_run_key TEXT,created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','stale','error')),FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id),FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id),FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL);
    CREATE TABLE evidence_assessment_decisions(evidence_assessment_decision_id INTEGER PRIMARY KEY,event_id_snapshot TEXT NOT NULL,evidence_id INTEGER NOT NULL,baseline_assessment_id INTEGER NOT NULL,baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),selected_probability INTEGER NOT NULL CHECK(selected_probability BETWEEN 0 AND 100),assessment_id INTEGER NOT NULL UNIQUE,run_id INTEGER NOT NULL,decision_source TEXT NOT NULL DEFAULT 'telegram',actor TEXT,decided_at TEXT NOT NULL,recommendation_id INTEGER REFERENCES evidence_assessment_recommendations(evidence_assessment_recommendation_id),FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id),FOREIGN KEY(assessment_id) REFERENCES assessments(assessment_id),FOREIGN KEY(run_id) REFERENCES runs(run_id));
    INSERT INTO events VALUES('event_a','Event A'); INSERT INTO runs VALUES(7);
    INSERT INTO assessments VALUES(12,'event_a',7,15); INSERT INTO assessments VALUES(13,'event_a',7,20);
    INSERT INTO sources VALUES(10,'Source'); INSERT INTO evidence_items VALUES(100,'event_a',10,7,'indicator','medium','Evidence A');
    INSERT INTO evidence_assessment_recommendations(evidence_assessment_recommendation_id,event_id_snapshot,evidence_id,baseline_assessment_id,baseline_probability,recommended_probability,probability_delta,recommended_band,recommended_label,recommendation_confidence,change_from_baseline,net_evidence_direction,net_evidence_impact,baseline_incorporation,category_transition,recommendation_rationale,delta_justification,methodology_version,parser_version,run_id,status) VALUES(31,'event_a',100,12,15,20,5,'15-25%','label','medium','increase','positive','moderate','medium',0,'rationale','delta','v1','v1',7,'ready');
    INSERT INTO evidence_assessment_decisions VALUES(41,'event_a',100,12,15,20,13,7,'telegram','telegram:123','2026-09-22T00:00:00Z',31);
    """); c.commit(); c.close()

def test_v15_migration_and_delete_preserve_provenance(tmp_path):
    db=tmp_path/"p.sqlite3"; make_v14_db(db)
    r=subprocess.run([sys.executable,str(MIGRATION),"--db",str(db)],capture_output=True,text=True); assert r.returncode==0, r.stderr
    cli=load_delete_cli(); args=Namespace(db=str(db),home=None,source="telegram",actor="telegram:123",evidence_id=100,event_id=None)
    assert cli.cmd_delete_evidence(args)==0
    c=sqlite3.connect(db); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON")
    assert c.execute("SELECT COUNT(*) FROM evidence_items WHERE evidence_id=100").fetchone()[0]==0
    assert c.execute("SELECT COUNT(*) FROM sources WHERE source_id=10").fetchone()[0]==1
    rec=c.execute("SELECT * FROM evidence_assessment_recommendations WHERE evidence_assessment_recommendation_id=31").fetchone()
    dec=c.execute("SELECT * FROM evidence_assessment_decisions WHERE evidence_assessment_decision_id=41").fetchone()
    assert rec is not None and rec["evidence_id_snapshot"]==100 and rec["evidence_id"] is None
    assert dec is not None and dec["evidence_id_snapshot"]==100 and dec["evidence_id"] is None and dec["recommendation_id"]==31
    assert c.execute("SELECT COUNT(*) FROM assessments WHERE assessment_id IN (12,13)").fetchone()[0]==2
    assert c.execute("SELECT COUNT(*) FROM deletion_audit WHERE target_type='evidence' AND target_id_snapshot='100'").fetchone()[0]==1
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
    c.close()
    assert cli.cmd_delete_evidence(args)==0
    c=sqlite3.connect(db); assert c.execute("SELECT COUNT(*) FROM deletion_audit WHERE target_type='evidence' AND target_id_snapshot='100'").fetchone()[0]==1; c.close()

def test_v15_preserves_ids_links_and_check_only(tmp_path):
    db=tmp_path/"p.sqlite3"; make_v14_db(db)
    assert subprocess.run([sys.executable,str(MIGRATION),"--db",str(db)]).returncode==0
    c=sqlite3.connect(db)
    assert c.execute("SELECT evidence_assessment_recommendation_id,evidence_id_snapshot,evidence_id FROM evidence_assessment_recommendations").fetchone()==(31,100,100)
    assert c.execute("SELECT evidence_assessment_decision_id,evidence_id_snapshot,evidence_id,recommendation_id FROM evidence_assessment_decisions").fetchone()==(41,100,100,31)
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='15'
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]; c.close()
    assert subprocess.run([sys.executable,str(MIGRATION),"--db",str(db),"--check-only"]).returncode==0
