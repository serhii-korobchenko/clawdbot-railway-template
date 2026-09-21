from __future__ import annotations
import importlib.util, json, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/"prorok"))
from prorok_calibration import calibration_math, CalibrationValidationError
from prorok_evidence_recommendation_parser import parse_evidence_recommendation, EvidenceRecommendationParseError

def payload(**overrides):
    p={"event_id":"event_a","evidence_id":8,"baseline_assessment_id":12,"baseline_probability":15,
       "recommended_probability":20,"recommended_band":"10-20%","recommended_label":"Ймовірність низька",
       "recommendation_confidence":"medium","change_from_baseline":"increase","probability_delta":5,
       "net_evidence_direction":"positive","net_evidence_impact":"moderate","baseline_incorporation":"low",
       "category_transition":False,"recommendation_rationale":"Material independent signal.",
       "delta_justification":"A five point move is proportionate without crossing a qualitative band."}
    p.update(overrides); return json.dumps(p,ensure_ascii=False)

def parse(text):
    return parse_evidence_recommendation(text,expected_event_id="event_a",expected_evidence_id=8,
      expected_baseline_assessment_id=12,expected_baseline_probability=15)

def test_calibration_allows_non_grid_historical_baseline():
    r=calibration_math(17,20)
    assert r["probability_delta"]==3
    assert r["change_from_baseline"]=="increase"
    assert r["category_transition"] is False

def test_calibration_rejects_non_grid_recommendation():
    try: calibration_math(15,17)
    except CalibrationValidationError: pass
    else: raise AssertionError("non-grid recommendation must fail")

def test_parser_accepts_valid_recommendation():
    r=parse(payload())
    assert r.recommended_probability==20 and r.probability_delta==5

def test_parser_accepts_no_change():
    r=parse(payload(recommended_probability=15,probability_delta=0,change_from_baseline="no_update"))
    assert r.recommended_probability==15 and r.probability_delta==0

def test_parser_validates_category_transition():
    r=parse(payload(recommended_probability=25,recommended_band="25-35%",recommended_label="Малоймовірно",
                    probability_delta=10,category_transition=True))
    assert r.category_transition is True

def test_parser_rejects_wrong_delta():
    try: parse(payload(probability_delta=10))
    except EvidenceRecommendationParseError: pass
    else: raise AssertionError("wrong delta must fail")

def test_parser_rejects_stale_identity():
    try:
        parse_evidence_recommendation(payload(),expected_event_id="event_a",expected_evidence_id=9,
          expected_baseline_assessment_id=12,expected_baseline_probability=15)
    except EvidenceRecommendationParseError: pass
    else: raise AssertionError("wrong evidence identity must fail")

def load_migration():
    path=ROOT/"prorok"/"migrations"/"013_evidence_assessment_recommendations.py"
    spec=importlib.util.spec_from_file_location("migration13",path); m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m

def test_v13_schema_keeps_legacy_decisions_nullable(tmp_path):
    m=load_migration(); db=tmp_path/"p.sqlite3"; c=sqlite3.connect(db)
    c.executescript("""CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT);
    INSERT INTO meta(key,value) VALUES('schema_version','12');
    CREATE TABLE runs(run_id INTEGER PRIMARY KEY);
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY);
    CREATE TABLE evidence_assessment_decisions(evidence_assessment_decision_id INTEGER PRIMARY KEY,event_id_snapshot TEXT,
      evidence_id INTEGER,baseline_assessment_id INTEGER,baseline_probability INTEGER,selected_probability INTEGER,
      assessment_id INTEGER,run_id INTEGER,decision_source TEXT,actor TEXT,decided_at TEXT);
    INSERT INTO evidence_assessment_decisions VALUES(1,'event_a',8,12,15,5,19,34,'telegram',NULL,'2026-09-20T00:00:00Z');""")
    c.executescript(m.RECOMMENDATIONS_DDL)
    c.execute("ALTER TABLE evidence_assessment_decisions ADD COLUMN recommendation_id INTEGER REFERENCES evidence_assessment_recommendations(evidence_assessment_recommendation_id)")
    row=c.execute("SELECT recommendation_id FROM evidence_assessment_decisions WHERE evidence_assessment_decision_id=1").fetchone()
    assert row[0] is None
    assert not m.validate(c)
    c.close()


def test_v13_ddl_statements_rollback_atomically(tmp_path):
    m=load_migration(); db=tmp_path/"atomic.sqlite3"; c=sqlite3.connect(db)
    c.executescript("""CREATE TABLE runs(run_id INTEGER PRIMARY KEY);
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY);""")
    c.commit()
    try:
        c.execute("BEGIN IMMEDIATE")
        for statement in m.RECOMMENDATIONS_DDL.split(";"):
            if statement.strip():
                c.execute(statement)
        raise RuntimeError("force rollback")
    except RuntimeError:
        c.rollback()
    table=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_assessment_recommendations'").fetchone()
    indexes=c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_evidence_assessment_recommendations_%'").fetchall()
    assert table is None
    assert indexes == []
    c.close()
