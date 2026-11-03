#!/usr/bin/env python3
"""Candidate Force: auditable background review of pending Candidate Evidence.

Positive/negative numeric delta promotes to official Evidence; zero delta records a
rejection in candidate_review_decisions, preserving the Candidate and recommendation.
No assessment probability is changed. Failed candidates remain pending.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid

try:
    from .prorok_candidate_review_cli import require_v16
    from .prorok_evidence_recommendation_cli import connect, resolve_db
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prorok_candidate_review_cli import require_v16
    from prorok_evidence_recommendation_cli import connect, resolve_db

DEFAULT_RUNS = Path("/data/workspace/prorok/candidate_force_runs")
RECOMMEND = Path(__file__).with_name("prorok_candidate_recommendation_cli.py")
REVIEW = Path(__file__).with_name("prorok_candidate_review_cli.py")
MAX_ITEMS = 1000

def pending_ids(conn):
    require_v16(conn)
    return [int(row[0]) for row in conn.execute("""
        SELECT ce.candidate_id FROM refresh_candidate_evidence ce
        JOIN refresh_event_results rer ON rer.refresh_event_result_id=ce.refresh_event_result_id
        LEFT JOIN refresh_user_decisions ud ON ud.refresh_event_result_id=ce.refresh_event_result_id
        LEFT JOIN candidate_review_decisions d ON d.candidate_id=ce.candidate_id
        LEFT JOIN refresh_candidate_promotions p ON p.candidate_id=ce.candidate_id
        WHERE ce.validation_state='accepted' AND rer.event_id IS NOT NULL
          AND ud.decision_id IS NULL AND d.candidate_id IS NULL AND p.candidate_id IS NULL
        ORDER BY ce.candidate_id
    """)]

def classify_recommendation(row, current_assessment_id):
    if row is None or row["status"] != "ready":
        raise ValueError("Candidate recommendation missing or not ready")
    if int(row["baseline_assessment_id"]) != int(current_assessment_id):
        raise ValueError("Candidate recommendation baseline is stale")
    delta = int(row["probability_delta"])
    if int(row["recommended_probability"]) - int(row["baseline_probability"]) != delta:
        raise ValueError("Inconsistent probability delta")
    if row["change_from_baseline"] != ("increase" if delta > 0 else "decrease" if delta < 0 else "no_update"):
        raise ValueError("Inconsistent recommendation direction")
    return "accept" if delta != 0 else "reject"

def load_recommendation(conn, candidate_id, recommendation_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute("""SELECT r.*, ce.validation_state, rer.event_id
        FROM candidate_assessment_recommendations r
        JOIN refresh_candidate_evidence ce ON ce.candidate_id=r.candidate_id
        JOIN refresh_event_results rer ON rer.refresh_event_result_id=ce.refresh_event_result_id
        WHERE r.candidate_assessment_recommendation_id=? AND r.candidate_id=?""",
        (recommendation_id, candidate_id)).fetchone()
    if not row or row["validation_state"] != "accepted" or str(row["event_id"]) != str(row["event_id_snapshot"]):
        raise ValueError("Candidate/recommendation mismatch")
    assessment = conn.execute("""SELECT assessment_id FROM assessments WHERE event_id=?
        ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1""",(row["event_id"],)).fetchone()
    if not assessment:
        raise ValueError("Event assessment missing")
    return row, int(assessment[0])

def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def write_state(directory, run_id, state):
    path = directory / (run_id + ".json")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as tmp:
        os.chmod(tmp.name, 0o600)
        json.dump(state, tmp, ensure_ascii=False, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        name = tmp.name
    os.replace(name, path)

def latest_state(directory):
    if not directory.exists():
        return None
    names = sorted(directory.glob("*.json"), reverse=True)
    for name in names:
        try:
            return json.loads(name.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
    return None

def subprocess_checked(args, timeout):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"CLI exit {p.returncode}: {(p.stderr or p.stdout).strip()[:350]}")
    return p.stdout

def recommendation_id(stdout):
    match = re.search(r"candidate_assessment_recommendation_id:\s*(\d+)", stdout)
    if not match:
        raise ValueError("Recommendation CLI did not return recommendation ID")
    return int(match.group(1))

def run_worker(db, directory, run_id, actor):
    state = {"run_id":run_id, "status":"running", "started_at":utcnow(),
             "finished_at":None,"actor":actor,"total":0,"evaluated":0,
             "promoted":0,"rejected":0,"failed":0,"skipped":0,"results":[]}
    write_state(directory, run_id, state)
    try:
        with connect(db) as conn:
            ids = pending_ids(conn)
        state["total"] = len(ids)
        write_state(directory, run_id, state)
        for candidate_id in ids:
            entry = {"candidate_id":candidate_id}
            try:
                # Another actor may have reviewed this candidate while the batch runs.
                with connect(db) as conn:
                    if candidate_id not in pending_ids(conn):
                        entry["result"]="skipped";state["skipped"]+=1
                        state["results"].append(entry);write_state(directory, run_id, state)
                        continue
                output = subprocess_checked([sys.executable,str(RECOMMEND),str(candidate_id),"--db",str(db)], 240)
                rid = recommendation_id(output)
                state["evaluated"] += 1
                with connect(db) as conn:
                    row, current_id = load_recommendation(conn, candidate_id, rid)
                    decision = classify_recommendation(row, current_id)
                    delta = int(row["probability_delta"])
                # Transactional decision writer rejects duplicate reviews and promotions.
                subprocess_checked([sys.executable,str(REVIEW),"--db",str(db),"decide",
                    str(candidate_id),decision,"--recommendation-id",str(rid),
                    "--source","system","--actor",f"candidate_force:{run_id}"], 45)
                entry.update({"result":"promoted" if decision=="accept" else "rejected",
                              "recommendation_id":rid,"delta":delta})
                state["promoted" if decision=="accept" else "rejected"] += 1
            except (OSError, sqlite3.Error, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                # Never promote/reject a candidate whose assessment is unverified.
                state["failed"] += 1
                entry.update({"result":"failed","error":str(exc)[:350]})
            state["results"].append(entry)
            write_state(directory, run_id, state)
        state["status"]="completed"
    except Exception as exc:
        state["status"]="failed"
        state["error"]=str(exc)[:350]
    finally:
        state["finished_at"]=utcnow()
        write_state(directory, run_id, state)
    return 0 if state["status"]=="completed" else 1

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path)
    p.add_argument("--runs-dir",type=Path,default=DEFAULT_RUNS)
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("preview")
    sub.add_parser("status")
    start=sub.add_parser("start")
    start.add_argument("--actor",default="telegram")
    worker=sub.add_parser("_worker")
    worker.add_argument("run_id")
    worker.add_argument("--actor",required=True)
    worker.add_argument("--lock-fd",type=int,required=True)
    args=p.parse_args(argv)
    db=resolve_db(str(args.db) if args.db else None)
    if args.command=="status":
        print(json.dumps(latest_state(args.runs_dir) or {"status":"never_run"},ensure_ascii=False))
        return 0
    if args.command=="preview":
        with connect(db) as conn:
            ids=pending_ids(conn)
        print(json.dumps({"pending":len(ids),"limit":MAX_ITEMS,"can_start":len(ids)<=MAX_ITEMS}))
        return 0
    if args.command=="_worker":
        # Keep the inherited lock descriptor; opening process.lock a second time
        # would deadlock against the lock inherited from the parent.
        os.fstat(args.lock_fd)
        try:
            return run_worker(db,args.runs_dir,args.run_id,args.actor)
        finally:
            os.close(args.lock_fd)
    args.runs_dir.mkdir(parents=True,exist_ok=True)
    fd=os.open(str(args.runs_dir / "process.lock"),os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"status":"already_running"}))
        os.close(fd)
        return 0
    with connect(db) as conn:
        ids=pending_ids(conn)
    if len(ids)>MAX_ITEMS:
        print(json.dumps({"status":"too_many","pending":len(ids),"limit":MAX_ITEMS}))
        os.close(fd)
        return 2
    run_id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid.uuid4().hex[:8]
    command=[sys.executable,str(Path(__file__).resolve()),"--db",str(db),
             "--runs-dir",str(args.runs_dir),"_worker",run_id,"--actor",args.actor,"--lock-fd",str(fd)]
    try:
        # Inheriting the flock descriptor prevents concurrent starts while the worker runs.
        with open(os.devnull,"wb") as null:
            child=subprocess.Popen(command,stdin=null,stdout=null,stderr=null,
                                   start_new_session=True,pass_fds=(fd,))
    finally:
        os.close(fd)
    print(json.dumps({"status":"started","run_id":run_id,"pid":child.pid,
                      "pending":len(ids)},ensure_ascii=False))
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Candidate Force error: {exc}",file=sys.stderr)
        raise SystemExit(1)
