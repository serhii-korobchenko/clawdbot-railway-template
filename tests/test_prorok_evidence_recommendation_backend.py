from __future__ import annotations
import json, sqlite3, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"prorok"))
from prorok_evidence_recommendation_cli import CliError, extract_agent_report, load_context, persist_report, run_openclaw_agent

DDL=(ROOT/"prorok"/"migrations"/"013_evidence_assessment_recommendations.py").read_text(encoding="utf-8")
ns={}; exec(compile(DDL,"migration13","exec"),ns)
RECOMMENDATIONS_DDL=ns["RECOMMENDATIONS_DDL"]

def make_db(path: Path):
    c=sqlite3.connect(path)
    c.row_factory=sqlite3.Row
    c.executescript("""CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT);
    INSERT INTO meta(key,value) VALUES('schema_version','13');
    CREATE TABLE events(event_id TEXT PRIMARY KEY,title TEXT,question TEXT,forecast_horizon TEXT,decision_criteria TEXT);
    CREATE TABLE runs(run_id INTEGER PRIMARY KEY);
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY,event_id TEXT,probability_percent INTEGER,confidence TEXT,rationale TEXT,assessed_at TEXT);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY,event_id TEXT,source_id INTEGER,run_id INTEGER,direction TEXT,strength TEXT,summary TEXT,relevance INTEGER,credibility INTEGER,created_at TEXT);
    CREATE TABLE evidence_assessment_decisions(evidence_assessment_decision_id INTEGER PRIMARY KEY,recommendation_id INTEGER);
    INSERT INTO events VALUES('event_a','Event A','Will A happen?','2026-12-31','Resolve yes if A happens.');
    INSERT INTO assessments VALUES(12,'event_a',15,'medium','baseline','2026-09-20T00:00:00Z');
    INSERT INTO evidence_items(evidence_id,event_id,direction,strength,summary,relevance,credibility,created_at) VALUES(8,'event_a','indicator','medium','Official evidence summary',90,85,'2026-09-21T00:00:00Z');
    """)
    for statement in RECOMMENDATIONS_DDL.split(";"):
        if statement.strip(): c.execute(statement)
    c.commit(); return c

def report(**kw):
    d={"event_id":"event_a","evidence_id":8,"baseline_assessment_id":12,"baseline_probability":15,
       "recommended_probability":20,"recommended_band":"10-20%","recommended_label":"Ймовірність низька",
       "recommendation_confidence":"medium","change_from_baseline":"increase","probability_delta":5,
       "net_evidence_direction":"positive","net_evidence_impact":"moderate","baseline_incorporation":"low",
       "category_transition":False,"recommendation_rationale":"Material independent signal.",
       "delta_justification":"Five points is proportionate and remains inside the current band."}
    d.update(kw); return json.dumps(d,ensure_ascii=False)

def test_recommendation_persists_without_changing_official_assessment(tmp_path):
    c=make_db(tmp_path/"p.sqlite3"); ctx=load_context(c,"event_a",8)
    before=c.execute("SELECT COUNT(*),MAX(probability_percent) FROM assessments").fetchone()
    rid=persist_report(c,ctx,report(),agent_id="prorok-refresh",model_used="test-model")
    after=c.execute("SELECT COUNT(*),MAX(probability_percent) FROM assessments").fetchone()
    row=c.execute("SELECT * FROM evidence_assessment_recommendations WHERE evidence_assessment_recommendation_id=?",(rid,)).fetchone()
    assert tuple(before)==tuple(after)==(1,15)
    assert row["recommended_probability"]==20
    assert row["baseline_assessment_id"]==12
    assert row["methodology_version"]=="refresh-calibration-v1"
    c.close()

def test_no_change_recommendation_is_valid(tmp_path):
    c=make_db(tmp_path/"p.sqlite3"); ctx=load_context(c,"event_a",8)
    rid=persist_report(c,ctx,report(recommended_probability=15,probability_delta=0,change_from_baseline="no_update"))
    row=c.execute("SELECT recommended_probability,probability_delta FROM evidence_assessment_recommendations WHERE evidence_assessment_recommendation_id=?",(rid,)).fetchone()
    assert tuple(row)==(15,0); c.close()

def test_stale_baseline_is_rejected_before_persist(tmp_path):
    c=make_db(tmp_path/"p.sqlite3"); ctx=load_context(c,"event_a",8)
    c.execute("INSERT INTO assessments VALUES(13,'event_a',25,'medium','new baseline','2026-09-21T00:00:00Z')"); c.commit()
    try: persist_report(c,ctx,report())
    except CliError as exc: assert "stale baseline" in str(exc)
    else: raise AssertionError("stale recommendation must fail")
    assert c.execute("SELECT COUNT(*) FROM evidence_assessment_recommendations").fetchone()[0]==0
    c.close()

def test_wrong_evidence_event_is_rejected(tmp_path):
    c=make_db(tmp_path/"p.sqlite3")
    c.execute("INSERT INTO events VALUES('event_b','B','B?','2026-12-31','B')"); c.commit()
    try: load_context(c,"event_b",8)
    except CliError as exc: assert "does not belong" in str(exc)
    else: raise AssertionError("cross-event evidence must fail")
    c.close()

def test_agent_derived_delta_is_normalized_before_persistence(tmp_path):
    c=make_db(tmp_path/"p.sqlite3"); ctx=load_context(c,"event_a",8)
    rid=persist_report(c,ctx,report(probability_delta=10))
    row=c.execute(
        "SELECT probability_delta FROM evidence_assessment_recommendations "
        "WHERE evidence_assessment_recommendation_id=?",(rid,)
    ).fetchone()
    assert row[0]==5
    c.close()


def agent_envelope(payload: str, model: str = "test-model"):
    return json.dumps({"response": payload, "model": model}, ensure_ascii=False)


def test_openclaw_agent_success_returns_strict_report():
    def runner(cmd, **kwargs):
        assert cmd[:3] == ["openclaw", "agent", "--agent"]
        assert "--message" in cmd and "--json" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=agent_envelope(report()), stderr="")
    payload, model = run_openclaw_agent("prompt", runner=runner)
    assert json.loads(payload)["recommended_probability"] == 20
    assert model == "test-model"


def test_openclaw_2026_5_22_payload_envelope_is_supported():
    envelope = {
        "result": {
            "payloads": [{"text": report()}],
            "meta": {"agentMeta": {"model": "gpt-5.4-mini"}},
        }
    }
    payload, model = extract_agent_report(json.dumps(envelope))
    assert json.loads(payload)["recommended_probability"] == 20
    assert model == "gpt-5.4-mini"


def test_openclaw_agent_failure_does_not_persist(tmp_path):
    c=make_db(tmp_path/"p.sqlite3")
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="gateway unavailable")
    try:
        run_openclaw_agent("prompt", runner=runner)
    except CliError as exc:
        assert "exit code 2" in str(exc)
    else:
        raise AssertionError("agent failure must fail")
    assert c.execute("SELECT COUNT(*) FROM evidence_assessment_recommendations").fetchone()[0] == 0
    c.close()


def test_openclaw_agent_timeout_is_explicit():
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    try:
        run_openclaw_agent("prompt", timeout_seconds=7, runner=runner)
    except CliError as exc:
        assert "timed out after 7s" in str(exc)
    else:
        raise AssertionError("timeout must fail")


def test_invalid_openclaw_json_envelope_is_rejected():
    try:
        extract_agent_report("not-json")
    except CliError as exc:
        assert "invalid JSON envelope" in str(exc)
    else:
        raise AssertionError("invalid OpenClaw envelope must fail")


def test_openclaw_envelope_without_strict_report_is_rejected():
    try:
        extract_agent_report(json.dumps({"response":"plain prose"}))
    except CliError as exc:
        assert "does not contain" in str(exc)
    else:
        raise AssertionError("plain prose must fail")


def test_cli_exposes_non_persisting_dry_run_json_mode():
    source=(ROOT/"prorok"/"prorok_evidence_recommendation_cli.py").read_text(encoding="utf-8")
    assert '"--dry-run-json"' in source
    assert "if a.dry_run_json:" in source
    assert "print(report)" in source


def test_prompt_requires_ukrainian_user_facing_explanations(tmp_path):
    c=make_db(tmp_path/"p.sqlite3"); ctx=load_context(c,"event_a",8)
    prompt=build_prompt(ctx)
    assert "Always write all user-facing explanatory text in Ukrainian" in prompt
    assert "recommendation_rationale and delta_justification MUST be in Ukrainian" in prompt
