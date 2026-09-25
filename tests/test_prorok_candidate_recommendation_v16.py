from __future__ import annotations
import importlib.util, json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); m=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(m); return m

def make_db(path):
    c=sqlite3.connect(path); c.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO meta VALUES('schema_version','16');
    CREATE TABLE events(event_id TEXT PRIMARY KEY,title TEXT,question TEXT,forecast_horizon TEXT,decision_criteria TEXT);
    INSERT INTO events VALUES('event-a','Event A','Will A happen?','2026-12-31','Resolve on occurrence');
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY,event_id TEXT,probability_percent INTEGER,confidence TEXT,rationale TEXT,assessed_at TEXT);
    INSERT INTO assessments VALUES(1,'event-a',35,'medium','baseline','2026-09-01');
    CREATE TABLE refresh_event_results(refresh_event_result_id INTEGER PRIMARY KEY,event_id TEXT,event_title_snapshot TEXT); INSERT INTO refresh_event_results VALUES(1,'event-a','Event A');
    CREATE TABLE refresh_candidate_evidence(candidate_id INTEGER PRIMARY KEY,refresh_event_result_id INTEGER,validation_state TEXT,direction TEXT,strength TEXT,relevance TEXT,credibility TEXT,summary TEXT);
    INSERT INTO refresh_candidate_evidence VALUES(7,1,'accepted','indicator','medium','high','high','Candidate summary');
    CREATE TABLE candidate_assessment_recommendations(candidate_assessment_recommendation_id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL,event_id_snapshot TEXT NOT NULL,baseline_assessment_id INTEGER NOT NULL,baseline_probability INTEGER NOT NULL,recommended_probability INTEGER NOT NULL,probability_delta INTEGER NOT NULL,recommended_band TEXT,recommended_label TEXT,recommendation_confidence TEXT,change_from_baseline TEXT,net_evidence_direction TEXT,net_evidence_impact TEXT,baseline_incorporation TEXT,category_transition INTEGER,recommendation_rationale TEXT,delta_justification TEXT,methodology_version TEXT,parser_version TEXT,agent_id TEXT,model_used TEXT,run_id INTEGER,source_run_key TEXT,status TEXT);
    """); c.commit(); c.close()

def report():
    return json.dumps({'event_id':'event-a','evidence_id':7,'baseline_assessment_id':1,'baseline_probability':35,'recommended_probability':40,'recommended_band':'40-50%','recommended_label':'Реалістична можливість','recommendation_confidence':'medium','change_from_baseline':'increase','probability_delta':5,'net_evidence_direction':'positive','net_evidence_impact':'moderate','baseline_incorporation':'low','category_transition':True,'recommendation_rationale':'Кандидат містить нову релевантну інформацію.','delta_justification':'Помірний вплив обґрунтовує підвищення на 5 пунктів.'},ensure_ascii=False)

def test_candidate_context_uses_event_and_current_baseline(tmp_path):
    db=tmp_path/'d.sqlite'; make_db(db); m=load('prorok/prorok_candidate_recommendation_cli.py','cr1')
    with m.official.connect(db) as c:
        x=m.load_context(c,7); assert (x.event_id,x.evidence_id,x.baseline_assessment_id,x.baseline_probability)==('event-a',7,1,35)
        p=m.build_prompt(x,7); assert 'Candidate Evidence' in p and 'NOT official evidence' in p

def test_persist_candidate_recommendation_only(tmp_path):
    db=tmp_path/'d.sqlite'; make_db(db); m=load('prorok/prorok_candidate_recommendation_cli.py','cr2')
    with m.official.connect(db) as c:
        rid=m.persist_report(c,m.load_context(c,7),7,report(),agent_id='pytest')
        row=c.execute('SELECT candidate_id,event_id_snapshot,recommended_probability,probability_delta,status FROM candidate_assessment_recommendations WHERE candidate_assessment_recommendation_id=?',(rid,)).fetchone()
        assert tuple(row)==(7,'event-a',40,5,'ready')
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='evidence_items'").fetchone()[0]==0

def test_stale_baseline_blocks_persist(tmp_path):
    db=tmp_path/'d.sqlite'; make_db(db); m=load('prorok/prorok_candidate_recommendation_cli.py','cr3')
    with m.official.connect(db) as c:
        ctx=m.load_context(c,7); c.execute("INSERT INTO assessments VALUES(2,'event-a',45,'medium','new','2026-09-02')"); c.commit()
        try: m.persist_report(c,ctx,7,report())
        except m.CliError as e: assert 'stale baseline' in str(e)
        else: raise AssertionError('stale baseline accepted')

def test_nonaccepted_candidate_is_blocked(tmp_path):
    db=tmp_path/'d.sqlite'; make_db(db); m=load('prorok/prorok_candidate_recommendation_cli.py','cr4')
    c=sqlite3.connect(db); c.execute("UPDATE refresh_candidate_evidence SET validation_state='legacy_unvalidated' WHERE candidate_id=7"); c.commit(); c.close()
    with m.official.connect(db) as c:
        try: m.load_context(c,7)
        except m.CliError as e: assert 'not quarantine-accepted' in str(e)
        else: raise AssertionError('unvalidated candidate accepted')
