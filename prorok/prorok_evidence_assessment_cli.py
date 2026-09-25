#!/usr/bin/env python3
"""Create one explicit user assessment based on one official evidence item."""
from __future__ import annotations
import argparse, os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB="/data/workspace/prorok/prorok.sqlite3"
GRID=set(range(0,101,5))
BANDS=[
 (0,5,"0-5%","Віддалена можливість"),(10,20,"10-20%","Ймовірність низька"),
 (25,35,"25-35%","Малоймовірно"),(40,50,"40-50%","Реалістична можливість"),
 (55,75,"55-75%","Ймовірно"),(80,90,"80-90%","Висока ймовірність"),
 (95,100,"95-100%","Майже напевно")]
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
def band_for(p):
    for lo,hi,b,l in BANDS:
        if lo<=p<=hi: return b,l
    raise ValueError(f"probability {p}% falls in a canonical scale gap")
def db_path(v): return Path(v or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db"); p.add_argument("event_id"); p.add_argument("evidence_id",type=int)
    p.add_argument("--baseline-assessment-id",type=int,required=True); p.add_argument("--probability",type=int,required=True)
    p.add_argument("--source",default="telegram",choices=("telegram","manual_cli","system")); p.add_argument("--actor")
    p.add_argument("--recommendation-id",type=int,help="Optional selected-evidence recommendation provenance id")
    a=p.parse_args()
    if a.probability not in GRID: print("ERROR: --probability must use the canonical 5pp grid (0,5,...,100)",file=sys.stderr); return 1
    try: band,label=band_for(a.probability)
    except ValueError as e: print(f"ERROR: {e}",file=sys.stderr); return 1
    path=db_path(a.db)
    if not path.exists(): print(f"ERROR: DB not found: {path}",file=sys.stderr); return 1
    c=sqlite3.connect(str(path),timeout=30); c.row_factory=sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000"); c.execute("BEGIN IMMEDIATE")
        ev=c.execute("SELECT event_id,summary FROM evidence_items WHERE evidence_id=?",(a.evidence_id,)).fetchone()
        if ev is None or ev["event_id"]!=a.event_id: raise RuntimeError("evidence does not belong to the requested event")
        cur=c.execute("""SELECT assessment_id,probability_percent,confidence FROM assessments WHERE event_id=?
          ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1""",(a.event_id,)).fetchone()
        if cur is None: raise RuntimeError("event has no current assessment")
        if int(cur["assessment_id"])!=a.baseline_assessment_id:
            raise RuntimeError(f"stale baseline: expected assessment_id={a.baseline_assessment_id}, current={cur['assessment_id']}")
        baseline=int(cur["probability_percent"])
        recommendation_id=None
        if a.recommendation_id is not None:
            rec=c.execute("""SELECT evidence_assessment_recommendation_id,event_id_snapshot,evidence_id,
              baseline_assessment_id,baseline_probability,status
              FROM evidence_assessment_recommendations
              WHERE evidence_assessment_recommendation_id=?""",(a.recommendation_id,)).fetchone()
            if rec is None: raise RuntimeError(f"recommendation not found: {a.recommendation_id}")
            if str(rec["event_id_snapshot"])!=a.event_id or int(rec["evidence_id"])!=a.evidence_id:
                raise RuntimeError("recommendation does not belong to the requested event/evidence")
            if int(rec["baseline_assessment_id"])!=a.baseline_assessment_id or int(rec["baseline_probability"])!=baseline:
                raise RuntimeError("recommendation baseline does not match the current requested baseline")
            if str(rec["status"])!="ready":
                raise RuntimeError(f"recommendation is not actionable: status={rec['status']}")
            used=c.execute("SELECT evidence_assessment_decision_id FROM evidence_assessment_decisions WHERE recommendation_id=?",(a.recommendation_id,)).fetchone()
            if used is not None: raise RuntimeError(f"recommendation already has a decision: {used['evidence_assessment_decision_id']}")
            recommendation_id=a.recommendation_id
        ts=now()
        run=c.execute("INSERT INTO runs(run_type,status,notes) VALUES('manual_cli','running',?)",
          (f"Evidence-based assessment evidence_id={a.evidence_id} source={a.source}",)).lastrowid
        rationale=f"User assessment via {a.source} based on official evidence #{a.evidence_id}: {ev['summary']}"
        ass=c.execute("""INSERT INTO assessments(event_id,run_id,assessed_at,probability_percent,probability_band,
          probability_label,confidence,delta_from_previous,rationale) VALUES(?,?,?,?,?,?,?,?,?)""",
          (a.event_id,run,ts,a.probability,band,label,cur["confidence"] or "medium",a.probability-baseline,rationale)).lastrowid
        dec=c.execute("""INSERT INTO evidence_assessment_decisions(event_id_snapshot,evidence_id_snapshot,evidence_id,baseline_assessment_id,
          baseline_probability,selected_probability,assessment_id,run_id,decision_source,actor,decided_at,recommendation_id)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(a.event_id,a.evidence_id,a.evidence_id,a.baseline_assessment_id,baseline,a.probability,ass,run,a.source,a.actor,ts,recommendation_id)).lastrowid
        c.execute("UPDATE runs SET finished_at=?,status='completed',events_processed=1 WHERE run_id=?",(ts,run)); c.commit()
        print("OK: evidence-based assessment added"); print(f"evidence_assessment_decision_id: {dec}"); print(f"evidence_id: {a.evidence_id}")
        print(f"assessment_id: {ass}"); print(f"baseline_probability: {baseline}%"); print(f"selected_probability: {a.probability}%"); print(f"run_id: {run}")
        return 0
    except Exception as e:
        c.rollback(); print(f"ERROR: {e}",file=sys.stderr); return 1
    finally: c.close()
if __name__=="__main__": raise SystemExit(main())
