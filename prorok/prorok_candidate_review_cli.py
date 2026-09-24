#!/usr/bin/env python3
"""Independent review of one PROROK refresh Candidate Evidence item."""
from __future__ import annotations
import argparse, sqlite3, sys
from pathlib import Path
try:
    from .prorok_refresh_decision_cli import (
        CliError, connect, resolve_db, schema_version, fetch_one, utc_now,
        assert_candidate_source_not_already_official, upsert_candidate_source,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prorok_refresh_decision_cli import (
        CliError, connect, resolve_db, schema_version, fetch_one, utc_now,
        assert_candidate_source_not_already_official, upsert_candidate_source,
    )


def require_v16(c):
    try: v=int(schema_version(c) or 0)
    except ValueError: v=0
    if v < 16: raise CliError(f"schema v16+ required; current schema_version={schema_version(c)!r}")


def load_candidate(c,candidate_id):
    row=fetch_one(c,"""SELECT ce.*, rr.event_id, rr.event_title_snapshot
      FROM refresh_candidate_evidence ce
      JOIN refresh_event_results rr ON rr.refresh_event_result_id=ce.refresh_event_result_id
      WHERE ce.candidate_id=?""",(candidate_id,))
    if row is None: raise CliError(f"candidate_id not found: {candidate_id}")
    if row['event_id'] is None: raise CliError('candidate is detached from an event')
    return row


def existing_decision(c,candidate_id):
    return fetch_one(c,"SELECT * FROM candidate_review_decisions WHERE candidate_id=?",(candidate_id,))


def existing_promotion(c,candidate_id):
    return fetch_one(c,"SELECT * FROM refresh_candidate_promotions WHERE candidate_id=?",(candidate_id,))


def cmd_show(a):
    with connect(resolve_db(a)) as c:
        require_v16(c); x=load_candidate(c,a.candidate_id); d=existing_decision(c,a.candidate_id); p=existing_promotion(c,a.candidate_id)
        print(f"candidate_id: {x['candidate_id']}"); print(f"event_id: {x['event_id']}"); print(f"event: {x['event_title_snapshot']}")
        print(f"validation_state: {x['validation_state']}"); print(f"review_state: {d['decision_type'] if d else 'pending'}")
        print(f"direction: {x['direction']}"); print(f"strength: {x['strength']}"); print(f"source: {x['source']}"); print(f"url: {x['url']}"); print(f"summary: {x['summary']}")
        if p: print(f"official_evidence_id: {p['evidence_id']}")
    return 0


def create_run(c,candidate_id,source):
    cur=c.execute("INSERT INTO runs(run_type,status,notes) VALUES('manual_cli','running',?)",(f"Candidate review candidate_id={candidate_id} source={source}",)); return int(cur.lastrowid)


def finish_run(c,run_id,new_sources=0):
    c.execute("UPDATE runs SET finished_at=?,status='completed',events_processed=1,new_sources_found=? WHERE run_id=?",(utc_now(),new_sources,run_id))


def promote(c,x,review_id,run_id,now):
    candidate_id=int(x['candidate_id']); event_id=str(x['event_id'])
    if x['validation_state']!='accepted': raise CliError(f"candidate is not quarantine-accepted: validation_state={x['validation_state']}")
    if not str(x['url'] or '').strip(): raise CliError('candidate URL is required for promotion')
    if not str(x['summary'] or '').strip(): raise CliError('candidate summary is required for promotion')
    raw_url=str(x['url']).strip()
    try:
        assert_candidate_source_not_already_official(c,candidate_id=candidate_id,event_id=event_id,raw_url=raw_url)
    except CliError as exc:
        marker="candidate source already exists as official evidence for this event:"
        if marker not in str(exc): raise
        from prorok_refresh_decision_cli import canonicalize_url
        _canonical_url,canonical_hash,_domain=canonicalize_url(raw_url)
        existing=fetch_one(c,"""SELECT e.evidence_id,e.source_id FROM evidence_items e JOIN sources s ON s.source_id=e.source_id WHERE e.event_id=? AND s.canonical_url_hash=? ORDER BY e.evidence_id LIMIT 1""",(event_id,canonical_hash))
        if existing is None: raise
        evidence_id=int(existing['evidence_id'])
        c.execute("""INSERT INTO refresh_candidate_promotions(candidate_id,refresh_event_result_id,decision_id,candidate_review_decision_id,evidence_id,run_id,promotion_action,promoted_at)
          VALUES(?,?,NULL,?,?,?,?,?)""",(candidate_id,int(x['refresh_event_result_id']),review_id,evidence_id,run_id,'reused',now))
        return evidence_id,'reused',False
    source_id,new_source=upsert_candidate_source(c,x,now=now)
    cur=c.execute("""INSERT OR IGNORE INTO evidence_items(event_id,source_id,run_id,direction,strength,summary,relevance,credibility,created_at)
      VALUES(?,?,?,?,?,?,?,?,?)""",(event_id,source_id,run_id,x['direction'],x['strength'],str(x['summary']).strip(),x['relevance'],x['credibility'],now))
    if cur.rowcount==1: evidence_id=int(cur.lastrowid); action='inserted'
    else:
        e=fetch_one(c,"SELECT evidence_id FROM evidence_items WHERE event_id=? AND source_id=? AND direction=? AND summary=?",(event_id,source_id,x['direction'],str(x['summary']).strip()))
        if e is None: raise CliError('evidence deduplication lookup failed')
        evidence_id=int(e['evidence_id']); action='reused'
    c.execute("""INSERT INTO refresh_candidate_promotions(candidate_id,refresh_event_result_id,decision_id,candidate_review_decision_id,evidence_id,run_id,promotion_action,promoted_at)
      VALUES(?,?,NULL,?,?,?,?,?)""",(candidate_id,int(x['refresh_event_result_id']),review_id,evidence_id,run_id,action,now))
    return evidence_id,action,new_source


def cmd_decide(a):
    db=resolve_db(a); now=utc_now()
    with connect(db) as c:
      try:
        c.execute('BEGIN IMMEDIATE'); require_v16(c); x=load_candidate(c,a.candidate_id)
        old=existing_decision(c,a.candidate_id)
        if old:
            if old['decision_type']!=a.decision: raise CliError(f"candidate already finalized as {old['decision_type']}")
            p=existing_promotion(c,a.candidate_id); c.rollback(); print('OK: candidate decision already applied'); print(f"decision_type: {old['decision_type']}"); print(f"evidence_id: {p['evidence_id'] if p else 'none'}"); print('idempotent_replay: true'); return 0
        recommendation_id=a.recommendation_id
        if recommendation_id is not None:
            r=fetch_one(c,"SELECT candidate_id,event_id_snapshot FROM candidate_assessment_recommendations WHERE candidate_assessment_recommendation_id=?",(recommendation_id,))
            if r is None or int(r['candidate_id'])!=int(x['candidate_id']) or str(r['event_id_snapshot'])!=str(x['event_id']): raise CliError('recommendation does not belong to this Candidate/Event')
        cur=c.execute("INSERT INTO candidate_review_decisions(candidate_id,event_id_snapshot,decision_type,recommendation_id,decision_source,actor,decided_at) VALUES(?,?,?,?,?,?,?)",(int(x['candidate_id']),str(x['event_id']),a.decision,recommendation_id,a.source,a.actor,now)); review_id=int(cur.lastrowid)
        evidence_id=None
        if a.decision=='accept':
            run_id=create_run(c,int(x['candidate_id']),a.source); evidence_id,action,new_source=promote(c,x,review_id,run_id,now); finish_run(c,run_id,1 if new_source else 0)
        c.commit(); print('OK: candidate review decision applied'); print(f"candidate_id: {x['candidate_id']}"); print(f"event_id: {x['event_id']}"); print(f"decision_type: {a.decision}"); print(f"candidate_review_decision_id: {review_id}"); print(f"evidence_id: {evidence_id if evidence_id is not None else 'none'}"); return 0
      except Exception:
        c.rollback(); raise


def parser():
    p=argparse.ArgumentParser(); p.add_argument('--home'); p.add_argument('--db'); sub=p.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('show'); s.add_argument('candidate_id',type=int); s.set_defaults(fn=cmd_show)
    d=sub.add_parser('decide'); d.add_argument('candidate_id',type=int); d.add_argument('decision',choices=('accept','reject')); d.add_argument('--recommendation-id',type=int); d.add_argument('--source',default='manual_cli',choices=('telegram','manual_cli','system')); d.add_argument('--actor'); d.set_defaults(fn=cmd_decide)
    return p

def main():
    a=parser().parse_args()
    try: return a.fn(a)
    except (CliError,sqlite3.Error) as e: print(f"ERROR: {e}",file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
