from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "prorok" / "prorok_candidate_force_cli.py"
spec = importlib.util.spec_from_file_location("candidate_force_test", FILE)
force = importlib.util.module_from_spec(spec)
spec.loader.exec_module(force)


def make_db(path):
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
    INSERT INTO meta VALUES('schema_version','16');
    CREATE TABLE refresh_event_results(refresh_event_result_id INTEGER PRIMARY KEY,event_id TEXT);
    INSERT INTO refresh_event_results VALUES(1,'example');
    CREATE TABLE refresh_candidate_evidence(candidate_id INTEGER PRIMARY KEY,refresh_event_result_id INTEGER,validation_state TEXT);
    INSERT INTO refresh_candidate_evidence VALUES(1,1,'accepted'),(2,1,'accepted'),(3,1,'rejected');
    CREATE TABLE refresh_user_decisions(decision_id INTEGER PRIMARY KEY,refresh_event_result_id INTEGER);
    CREATE TABLE candidate_review_decisions(candidate_id INTEGER PRIMARY KEY,decision_type TEXT);
    CREATE TABLE refresh_candidate_promotions(candidate_id INTEGER PRIMARY KEY);
    """)
    c.close()


def test_only_pending_candidates_are_selected(tmp_path):
    db = tmp_path / "test.db"
    make_db(db)
    with force.connect(db) as c:
        assert force.pending_ids(c) == [1, 2]
        c.execute("INSERT INTO candidate_review_decisions VALUES(1,'reject')")
        assert force.pending_ids(c) == [2]
        c.execute("INSERT INTO refresh_candidate_promotions VALUES(2)")
        assert force.pending_ids(c) == []


@pytest.mark.parametrize("delta,direction,decision", [
    (5, "increase", "accept"),
    (-10, "decrease", "accept"),
    (0, "no_update", "reject"),
])
def test_change_classification(delta, direction, decision):
    row = dict(status="ready", baseline_assessment_id=7,
               baseline_probability=40, recommended_probability=40+delta,
               probability_delta=delta, change_from_baseline=direction)
    assert force.classify_recommendation(row, 7) == decision


def test_recommendation_must_be_current_and_consistent():
    row = dict(status="ready", baseline_assessment_id=7,
               baseline_probability=40, recommended_probability=45,
               probability_delta=5, change_from_baseline="increase")
    with pytest.raises(ValueError, match="stale"):
        force.classify_recommendation(row, 8)
    row["recommended_probability"] = 40
    with pytest.raises(ValueError, match="Inconsistent"):
        force.classify_recommendation(row, 7)


def test_audit_files_remain_readable(tmp_path):
    directory = tmp_path / "runs"
    force.write_state(directory, "20261103T180000Z-abcdef12",
                      dict(status="completed", promoted=2, rejected=1))
    assert force.latest_state(directory)["rejected"] == 1
    assert json.loads(next(directory.glob("*.json")).read_text())["promoted"] == 2


def test_telegram_confirmation_and_status_present():
    source = (ROOT / "prorok_telegram/src/index.js").read_text()
    for marker in ('"candidate-force-confirm"', '"candidate-force-apply"',
                   '"candidate-force-status"', 'candidateForceApplyPresentation'):
        assert marker in source


def test_worker_inherits_single_locked_file_descriptor():
    source = FILE.read_text()
    assert 'pass_fds=(fd,)' in source
    assert '"--lock-fd",str(fd)' in source
    assert "os.fstat(args.lock_fd)" in source


def test_empty_batch_does_not_spawn_background_worker(tmp_path, capsys, monkeypatch):
    db = tmp_path / "test.db"
    make_db(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO refresh_user_decisions VALUES(1,1)")
    def fail_spawn(*args, **kwargs):
        raise AssertionError("empty queue must not spawn a worker")
    monkeypatch.setattr(force.subprocess, "Popen", fail_spawn)
    assert force.main(["--db",str(db),"--runs-dir",str(tmp_path/"runs"),"start"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "empty"


def test_held_lock_is_detected_and_prevents_start(tmp_path, capsys):
    import fcntl
    import os
    db = tmp_path / "test.db"
    make_db(db)
    directory = tmp_path / "runs"
    directory.mkdir()
    fd = os.open(str(directory / "process.lock"),os.O_CREAT | os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert force.worker_active(directory)
        assert force.main(["--db",str(db),"--runs-dir",str(directory),"start"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "already_running"
    finally:
        os.close(fd)


def test_interrupted_run_is_visible(tmp_path):
    directory = tmp_path / "runs"
    force.write_state(directory,"20261103T190000Z-abcdef12",dict(status="running"))
    assert force.latest_state(directory)["status"] == "interrupted"
