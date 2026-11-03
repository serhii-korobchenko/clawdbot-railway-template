#!/usr/bin/env python3
"""Read and audit Evidence temporal provenance reviews; never update forecasts."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

try:
    from . import prorok_temporal_gate as temporal
    from .prorok_evidence_recommendation_cli import connect, resolve_db
except ImportError:
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    import prorok_temporal_gate as temporal
    from prorok_evidence_recommendation_cli import connect, resolve_db


def add_review(conn,kind,item_id,decision,rationale,actor):
    if not temporal._table_exists(conn,"evidence_temporal_reviews"):
        raise ValueError("schema v18 temporal reviews not installed")
    if decision not in {"verified_new","already_incorporated"}:
        raise ValueError("invalid decision")
    if len(rationale.strip()) < 12:
        raise ValueError("review rationale must contain at least 12 characters")
    if not actor.strip():
        raise ValueError("review actor required")
    conn.execute("BEGIN IMMEDIATE")
    try:
        state=temporal.inspect(conn,kind,item_id)
        conn.execute("""
            INSERT INTO evidence_temporal_reviews
                (target_type,target_id,baseline_assessment_id,decision,rationale,actor)
            VALUES(?,?,?,?,?,?)
        """,(kind,int(item_id),state["latest_assessment_id"],decision,rationale.strip(),actor.strip()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return temporal.inspect(conn,kind,item_id)


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--db")
    sub=p.add_subparsers(dest="command",required=True)
    show=sub.add_parser("show"); show.add_argument("kind",choices=("evidence","candidate"));show.add_argument("item_id",type=int)
    review=sub.add_parser("review");review.add_argument("kind",choices=("evidence","candidate"));review.add_argument("item_id",type=int)
    review.add_argument("decision",choices=("verified_new","already_incorporated"))
    review.add_argument("--rationale",required=True);review.add_argument("--actor",required=True)
    args=p.parse_args(argv)
    with connect(resolve_db(args.db)) as conn:
        if args.command=="show":
            result=temporal.inspect(conn,args.kind,args.item_id)
        else:
            result=add_review(conn,args.kind,args.item_id,args.decision,args.rationale,args.actor)
    print(json.dumps(result,ensure_ascii=False,default=str))
    return 0

if __name__=="__main__":
    try:
        raise SystemExit(main())
    except (ValueError,sqlite3.Error) as exc:
        print(f"Temporal review error: {exc}",file=sys.stderr)
        raise SystemExit(1)
