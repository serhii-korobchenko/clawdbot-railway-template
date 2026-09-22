from __future__ import annotations

import importlib.util
import sqlite3
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_db(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT);
    INSERT INTO meta VALUES('schema_version','16',NULL);
    CREATE TABLE events(event_id TEXT PRIMARY KEY,title TEXT);
    INSERT INTO events VALUES('event-a','Event A'),('event-b','Event B');
    CREATE TABLE runs(run_id INTEGER PRIMARY KEY,run_type TEXT,status TEXT,notes TEXT,started_at TEXT DEFAULT CURRENT_TIMESTAMP,finished_at TEXT,events_processed INTEGER,new_sources_found INTEGER);
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY,event_id TEXT,probability INTEGER,confidence TEXT,assessed_at TEXT);
    CREATE TABLE refresh_event_results(refresh_event_result_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,event_title_snapshot TEXT);
    INSERT INTO refresh_event_results VALUES(1,'event-a','Event A'),(2,'event-b','Event B');
    CREATE TABLE refresh_candidate_evidence(candidate_id INTEGER PRIMARY KEY,refresh_event_result_id INTEGER NOT NULL,validation_state TEXT NOT NULL,direction TEXT NOT NULL,strength TEXT,source TEXT,url TEXT,summary TEXT,relevance TEXT,credibility TEXT,published_at TEXT,title TEXT,canonical_url TEXT,source_policy_state TEXT,date_validation_state TEXT);
    INSERT INTO refresh_candidate_evidence VALUES(7,1,'accepted','indicator','medium','Source A','https://example.com/a','Candidate A','high','high','2026-09-01','A','https://example.com/a','accepted','accepted');
    INSERT INTO refresh_candidate_evidence VALUES(8,2,'accepted','counterindicator','weak','Source B','https://example.com/b','Candidate B','medium','high','2026-09-02','B','https://example.com/b','accepted','accepted');
    CREATE TABLE sources(source_id INTEGER PRIMARY KEY,url TEXT,canonical_url TEXT UNIQUE,title TEXT,publisher TEXT,published_at TEXT,accessed_at TEXT);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,source_id INTEGER NOT NULL,run_id INTEGER,direction TEXT NOT NULL,strength TEXT,summary TEXT,relevance TEXT,credibility TEXT,created_at TEXT,UNIQUE(event_id,source_id,direction,summary));
    CREATE TABLE refresh_user_decisions(decision_id INTEGER PRIMARY KEY);
    CREATE TABLE candidate_assessment_recommendations(candidate_assessment_recommendation_id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL,event_id_snapshot TEXT NOT NULL,baseline_assessment_id INTEGER NOT NULL);
    CREATE TABLE candidate_review_decisions(candidate_review_decision_id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL UNIQUE,event_id_snapshot TEXT NOT NULL,decision_type TEXT NOT NULL,recommendation_id INTEGER,decision_source TEXT NOT NULL,actor TEXT,decided_at TEXT NOT NULL);
    CREATE TABLE refresh_candidate_promotions(promotion_id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL UNIQUE,refresh_event_result_id INTEGER NOT NULL,decision_id INTEGER,candidate_review_decision_id INTEGER,evidence_id INTEGER,run_id INTEGER NOT NULL,promotion_action TEXT NOT NULL,promoted_at TEXT NOT NULL,CHECK((decision_id IS NOT NULL AND candidate_review_decision_id IS NULL) OR (decision_id IS NULL AND candidate_review_decision_id IS NOT NULL)));
    """)
    c.commit(); c.close()


def args(db: Path, candidate: int, decision: str) -> Namespace:
    return Namespace(db=str(db),home=None,candidate_id=candidate,decision=decision,recommendation_id=None,source='manual_cli',actor='pytest')


def test_candidate_event_affiliation_and_pending(tmp_path: Path) -> None:
    db=tmp_path/'db.sqlite3'; make_db(db); m=load('prorok/prorok_candidate_review_cli.py','candidate_review_a')
    with m.connect(db) as c:
        x=m.load_candidate(c,7)
        assert x['event_id']=='event-a'
        assert m.existing_decision(c,7) is None


def test_reject_is_final_without_promotion(tmp_path: Path) -> None:
    db=tmp_path/'db.sqlite3'; make_db(db); m=load('prorok/prorok_candidate_review_cli.py','candidate_review_b')
    assert m.cmd_decide(args(db,7,'reject'))==0
    c=sqlite3.connect(db)
    assert c.execute("SELECT decision_type,event_id_snapshot FROM candidate_review_decisions WHERE candidate_id=7").fetchone()==('reject','event-a')
    assert c.execute('SELECT COUNT(*) FROM refresh_candidate_promotions WHERE candidate_id=7').fetchone()[0]==0
    assert c.execute('SELECT COUNT(*) FROM evidence_items').fetchone()[0]==0
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    c.close()


def test_accept_promotes_to_same_event_and_is_idempotent(tmp_path: Path) -> None:
    db=tmp_path/'db.sqlite3'; make_db(db); m=load('prorok/prorok_candidate_review_cli.py','candidate_review_c')
    assert m.cmd_decide(args(db,7,'accept'))==0
    assert m.cmd_decide(args(db,7,'accept'))==0
    c=sqlite3.connect(db)
    row=c.execute("SELECT d.event_id_snapshot,p.decision_id,p.candidate_review_decision_id,p.evidence_id,e.event_id FROM candidate_review_decisions d JOIN refresh_candidate_promotions p ON p.candidate_review_decision_id=d.candidate_review_decision_id JOIN evidence_items e ON e.evidence_id=p.evidence_id WHERE d.candidate_id=7").fetchone()
    assert row[0]=='event-a' and row[1] is None and row[2] is not None and row[4]=='event-a'
    assert c.execute('SELECT COUNT(*) FROM candidate_review_decisions WHERE candidate_id=7').fetchone()[0]==1
    assert c.execute('SELECT COUNT(*) FROM refresh_candidate_promotions WHERE candidate_id=7').fetchone()[0]==1
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    c.close()


def test_conflicting_second_decision_is_rejected(tmp_path: Path) -> None:
    db=tmp_path/'db.sqlite3'; make_db(db); m=load('prorok/prorok_candidate_review_cli.py','candidate_review_d')
    assert m.cmd_decide(args(db,7,'reject'))==0
    try:
        m.cmd_decide(args(db,7,'accept'))
    except m.CliError as exc:
        assert 'already finalized as reject' in str(exc)
    else:
        raise AssertionError('conflicting final decision was accepted')


def test_recommendation_must_match_candidate_and_event(tmp_path: Path) -> None:
    db=tmp_path/'db.sqlite3'; make_db(db); m=load('prorok/prorok_candidate_review_cli.py','candidate_review_e')
    c=sqlite3.connect(db); c.execute("INSERT INTO assessments VALUES(1,'event-b',50,'medium','2026-09-01')"); c.execute("INSERT INTO candidate_assessment_recommendations VALUES(1,8,'event-b',1)"); c.commit(); c.close()
    a=args(db,7,'reject'); a.recommendation_id=1
    try:
        m.cmd_decide(a)
    except m.CliError as exc:
        assert 'does not belong to this Candidate/Event' in str(exc)
    else:
        raise AssertionError('cross-event recommendation was accepted')
