#!/usr/bin/env python3
"""PROROK schema v16: independent Candidate Review provenance."""
from __future__ import annotations
import argparse, os, sqlite3, sys
from pathlib import Path

DEFAULT_DB="/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION="16"

REC="""CREATE TABLE candidate_assessment_recommendations (
 candidate_assessment_recommendation_id INTEGER PRIMARY KEY,
 candidate_id INTEGER NOT NULL,
 event_id_snapshot TEXT NOT NULL,
 baseline_assessment_id INTEGER NOT NULL,
 baseline_probability INTEGER NOT NULL CHECK(baseline_probability BETWEEN 0 AND 100),
 recommended_probability INTEGER NOT NULL CHECK(recommended_probability BETWEEN 0 AND 100 AND recommended_probability % 5=0),
 probability_delta INTEGER NOT NULL,
 recommended_band TEXT NOT NULL,
 recommended_label TEXT NOT NULL,
 recommendation_confidence TEXT NOT NULL CHECK(recommendation_confidence IN ('low','medium','high')),
 change_from_baseline TEXT NOT NULL CHECK(change_from_baseline IN ('increase','decrease','no_update')),
 net_evidence_direction TEXT NOT NULL CHECK(net_evidence_direction IN ('positive','negative','balanced')),
 net_evidence_impact TEXT NOT NULL CHECK(net_evidence_impact IN ('none','weak','moderate','strong')),
 baseline_incorporation TEXT NOT NULL CHECK(baseline_incorporation IN ('low','medium','high')),
 category_transition INTEGER NOT NULL CHECK(category_transition IN (0,1)),
 recommendation_rationale TEXT NOT NULL,
 delta_justification TEXT NOT NULL,
 methodology_version TEXT NOT NULL,
 parser_version TEXT NOT NULL,
 agent_id TEXT, model_used TEXT, run_id INTEGER, source_run_key TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','stale','error')),
 FOREIGN KEY(candidate_id) REFERENCES refresh_candidate_evidence(candidate_id) ON DELETE RESTRICT,
 FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id),
 FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
)"""

DEC="""CREATE TABLE candidate_review_decisions (
 candidate_review_decision_id INTEGER PRIMARY KEY,
 candidate_id INTEGER NOT NULL UNIQUE,
 event_id_snapshot TEXT NOT NULL,
 decision_type TEXT NOT NULL CHECK(decision_type IN ('accept','reject')),
 recommendation_id INTEGER,
 decision_source TEXT NOT NULL DEFAULT 'telegram', actor TEXT,
 decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 FOREIGN KEY(candidate_id) REFERENCES refresh_candidate_evidence(candidate_id) ON DELETE RESTRICT,
 FOREIGN KEY(recommendation_id) REFERENCES candidate_assessment_recommendations(candidate_assessment_recommendation_id) ON DELETE SET NULL
)"""

PROM="""CREATE TABLE refresh_candidate_promotions_v16 (
 promotion_id INTEGER PRIMARY KEY AUTOINCREMENT,
 candidate_id INTEGER NOT NULL UNIQUE,
 refresh_event_result_id INTEGER NOT NULL,
 decision_id INTEGER,
 candidate_review_decision_id INTEGER,
 evidence_id INTEGER,
 run_id INTEGER NOT NULL,
 promotion_action TEXT NOT NULL CHECK(promotion_action IN ('inserted','reused')),
 promoted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 CHECK ((decision_id IS NOT NULL AND candidate_review_decision_id IS NULL) OR (decision_id IS NULL AND candidate_review_decision_id IS NOT NULL)),
 FOREIGN KEY(candidate_id) REFERENCES refresh_candidate_evidence(candidate_id) ON DELETE RESTRICT,
 FOREIGN KEY(refresh_event_result_id) REFERENCES refresh_event_results(refresh_event_result_id) ON DELETE RESTRICT,
 FOREIGN KEY(decision_id) REFERENCES refresh_user_decisions(decision_id) ON DELETE RESTRICT,
 FOREIGN KEY(candidate_review_decision_id) REFERENCES candidate_review_decisions(candidate_review_decision_id) ON DELETE RESTRICT,
 FOREIGN KEY(evidence_id) REFERENCES evidence_items(evidence_id) ON DELETE SET NULL,
 FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
)"""

def dbpath(x): return Path(x or os.getenv('PROROK_DB_PATH') or os.getenv('PROROK_DB') or DEFAULT_DB).expanduser().resolve()
def ver(c):
 r=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone(); return None if r is None else str(r[0])
def exists(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def validate(c):
 errors=[]
 for t in ('candidate_assessment_recommendations','candidate_review_decisions','refresh_candidate_promotions'):
  if not exists(c,t): errors.append('missing table: '+t)
 if errors: return errors
 info={r[1]:r for r in c.execute('PRAGMA table_info(refresh_candidate_promotions)')}
 if 'candidate_review_decision_id' not in info: errors.append('promotion review provenance missing')
 if 'decision_id' not in info or info['decision_id'][3]!=0: errors.append('promotion decision_id must be nullable')
 bad=c.execute('SELECT COUNT(*) FROM refresh_candidate_promotions WHERE (decision_id IS NULL)=(candidate_review_decision_id IS NULL)').fetchone()[0]
 if bad: errors.append(f'invalid promotion provenance rows: {bad}')
 if c.execute('PRAGMA foreign_key_check').fetchall(): errors.append('foreign_key_check failed')
 return errors

def migrate(c):
 old=[tuple(r) for r in c.execute('SELECT promotion_id,candidate_id,refresh_event_result_id,decision_id,evidence_id,run_id,promotion_action,promoted_at FROM refresh_candidate_promotions ORDER BY promotion_id')]
 c.execute(REC); c.execute(DEC); c.execute(PROM)
 c.execute('''INSERT INTO refresh_candidate_promotions_v16(promotion_id,candidate_id,refresh_event_result_id,decision_id,candidate_review_decision_id,evidence_id,run_id,promotion_action,promoted_at) SELECT promotion_id,candidate_id,refresh_event_result_id,decision_id,NULL,evidence_id,run_id,promotion_action,promoted_at FROM refresh_candidate_promotions''')
 c.execute('DROP TABLE refresh_candidate_promotions'); c.execute('ALTER TABLE refresh_candidate_promotions_v16 RENAME TO refresh_candidate_promotions')
 c.executescript('''CREATE INDEX idx_candidate_recommendations_candidate ON candidate_assessment_recommendations(candidate_id,created_at DESC,candidate_assessment_recommendation_id DESC); CREATE INDEX idx_candidate_recommendations_event ON candidate_assessment_recommendations(event_id_snapshot,created_at DESC,candidate_assessment_recommendation_id DESC); CREATE INDEX idx_candidate_review_event ON candidate_review_decisions(event_id_snapshot,decided_at DESC,candidate_review_decision_id DESC); CREATE INDEX idx_candidate_review_recommendation ON candidate_review_decisions(recommendation_id); CREATE INDEX idx_refresh_candidate_promotions_decision ON refresh_candidate_promotions(decision_id); CREATE INDEX idx_refresh_candidate_promotions_review ON refresh_candidate_promotions(candidate_review_decision_id); CREATE INDEX idx_refresh_candidate_promotions_evidence ON refresh_candidate_promotions(evidence_id);''')
 new=[tuple(r) for r in c.execute('SELECT promotion_id,candidate_id,refresh_event_result_id,decision_id,evidence_id,run_id,promotion_action,promoted_at FROM refresh_candidate_promotions ORDER BY promotion_id')]
 if new!=old: raise RuntimeError('existing promotion rows changed')
 c.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",(TARGET_VERSION,))

def main():
 p=argparse.ArgumentParser(); p.add_argument('--db'); p.add_argument('--check-only',action='store_true'); a=p.parse_args(); path=dbpath(a.db)
 if not path.exists(): print(f'ERROR: DB not found: {path}',file=sys.stderr); return 1
 c=sqlite3.connect(str(path),timeout=30)
 try:
  c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=5000'); before=ver(c)
  if a.check_only:
   e=validate(c)
   if before!=TARGET_VERSION: e.append(f"schema_version expected 16, got {before!r}")
   if e:
    for x in e: print('ERROR:',x)
    return 1
   print('OK: PROROK schema v16 verified'); return 0
  if before!='15': raise RuntimeError(f"schema_version expected 15 before migration, got {before!r}")
  c.execute('PRAGMA foreign_keys=OFF'); c.execute('BEGIN IMMEDIATE'); migrate(c)
  e=validate(c)
  if e: raise RuntimeError('; '.join(e))
  c.commit(); c.execute('PRAGMA foreign_keys=ON')
  print('OK: PROROK schema migration v16 applied'); print('schema_version_after: 16'); print('existing promotions preserved: yes'); print('foreign_key_check: ok'); return 0
 except Exception as e:
  c.rollback(); print('ERROR:',e,file=sys.stderr); return 1
 finally: c.close()
if __name__=='__main__': raise SystemExit(main())
