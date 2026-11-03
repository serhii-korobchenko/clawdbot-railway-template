from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from prorok import prorok_temporal_gate as temporal
from prorok import prorok_temporal_review_cli as review

ROOT=Path(__file__).resolve().parents[1]
MIG=ROOT / "prorok" / "migrations" / "018_evidence_temporal_reviews.py"


def make_db(tmp_path):
    db=tmp_path/"p.sqlite3"
    conn=sqlite3.connect(db)
    conn.row_factory=sqlite3.Row
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
    INSERT INTO meta VALUES('schema_version','17');
    CREATE TABLE assessments(assessment_id INTEGER PRIMARY KEY,event_id TEXT,assessed_at TEXT);
    INSERT INTO assessments VALUES(1,'event-a','2026-10-20T10:00:00Z');
    CREATE TABLE sources(source_id INTEGER PRIMARY KEY,published_at TEXT);
    INSERT INTO sources VALUES(1,'2026-10-10T09:00:00Z'),
        (2,'2026-10-21T11:00:00Z'),(3,NULL);
    CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY,event_id TEXT,
        source_id INTEGER,created_at TEXT);
    INSERT INTO evidence_items VALUES(11,'event-a',1,'2026-10-25T10:00:00Z'),
        (12,'event-a',2,'2026-10-25T10:00:00Z'),
        (13,'event-a',3,'2026-10-25T10:00:00Z');
    CREATE TABLE refresh_event_results(refresh_event_result_id INTEGER PRIMARY KEY,event_id TEXT);
    INSERT INTO refresh_event_results VALUES(1,'event-a');
    CREATE TABLE refresh_candidate_evidence(candidate_id INTEGER PRIMARY KEY,
        refresh_event_result_id INTEGER,published_at TEXT,created_at TEXT);
    INSERT INTO refresh_candidate_evidence VALUES(127,1,'2026-10-10','2026-10-25');
    """)
    return conn


def install_schema(conn):
    spec=importlib.util.spec_from_file_location("temporal_migration",MIG)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.migrate(conn)
    conn.commit()


def test_publication_date_only_is_conservative():
    assert temporal.classify_publication("2026-10-21","2026-10-20T10:00:00Z") == "newer_publication"
    assert temporal.classify_publication("2026-10-20","2026-10-20T10:00:00Z") == "review_required"
    assert temporal.classify_publication("2026-10-10","2026-10-20T10:00:00Z") == "review_required"
    assert temporal.classify_publication(None,"2026-10-20T10:00:00Z") == "unknown_date"
    assert temporal.classify_publication("2026-11-03T10:00:00","2026-10-20T10:00:00Z") == "unknown_date"


def test_old_evidence_blocked_until_audited_against_current_baseline(tmp_path):
    c=make_db(tmp_path);install_schema(c)
    with pytest.raises(ValueError,match="temporal provenance blocked"):
        temporal.require_allowed(c,"evidence",11)
    checked=review.add_review(c,"evidence",11,"verified_new",
        "Independent historical fact not priced into prior forecast","analyst")
    assert checked["status"] == "verified_new"
    assert temporal.require_allowed(c,"evidence",11)["automated_reassessment_allowed"]
    c.execute("INSERT INTO assessments VALUES(2,'event-a','2026-10-26T10:00:00Z')")
    c.commit()
    assert temporal.inspect(c,"evidence",11)["status"] == "review_required"
    with pytest.raises(ValueError):
        temporal.require_allowed(c,"evidence",11)
    c.close()


def test_duplicate_or_unknown_provenance_never_auto_reassessed(tmp_path):
    c=make_db(tmp_path);install_schema(c)
    assert temporal.inspect(c,"evidence",12)["status"]=="newer_publication"
    assert temporal.inspect(c,"evidence",13)["status"]=="unknown_date"
    assert temporal.inspect(c,"candidate",127)["status"]=="review_required"
    with pytest.raises(ValueError):
        temporal.require_allowed(c,"candidate",127)
    review.add_review(c,"evidence",11,"already_incorporated",
        "Already counted in the prior official assessment","analyst")
    assert temporal.inspect(c,"evidence",11)["status"]=="already_incorporated"
    c.close()


def test_review_is_append_only_requires_rationale_and_actor(tmp_path):
    c=make_db(tmp_path);install_schema(c)
    with pytest.raises(ValueError,match="rationale"):
        review.add_review(c,"evidence",11,"verified_new","short","analyst")
    with pytest.raises(ValueError,match="actor"):
        review.add_review(c,"evidence",11,"verified_new","Detailed justification"," ")
    review.add_review(c,"evidence",11,"verified_new","New corroborated information","analyst")
    review.add_review(c,"evidence",11,"already_incorporated","Already captured earlier","analyst")
    assert c.execute("SELECT COUNT(*) FROM evidence_temporal_reviews").fetchone()[0] == 2
    assert temporal.inspect(c,"evidence",11)["status"]=="already_incorporated"
    c.close()


def test_previously_assessed_evidence_is_blocked_even_with_newer_date(tmp_path):
    c=make_db(tmp_path);install_schema(c)
    c.executescript("""
        CREATE TABLE evidence_assessment_decisions(
            evidence_assessment_decision_id INTEGER PRIMARY KEY,
            evidence_id INTEGER NOT NULL);
        INSERT INTO evidence_assessment_decisions VALUES(99,12);
    """)
    state=temporal.inspect(c,"evidence",12)
    assert state["status"] == "already_incorporated"
    with pytest.raises(ValueError,match="already_incorporated"):
        temporal.require_allowed(c,"evidence",12)
    c.close()


def test_v18_gate_does_not_mutate_legacy_databases(tmp_path):
    c=make_db(tmp_path)
    assert temporal.enforce_if_enabled(c,"evidence",11) is None
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "17"
    with pytest.raises(ValueError):
        temporal.require_allowed(c,"evidence",11)
    c.close()


def test_confirmed_duplicate_overrides_newer_publication(tmp_path):
    c=make_db(tmp_path);install_schema(c)
    assert temporal.inspect(c,"evidence",12)["status"]=="newer_publication"
    review.add_review(c,"evidence",12,"already_incorporated",
                      "This newer posting repeats information already assessed","analyst")
    state=temporal.inspect(c,"evidence",12)
    assert state["status"]=="already_incorporated"
    assert state["automated_reassessment_allowed"] is False
    c.close()
