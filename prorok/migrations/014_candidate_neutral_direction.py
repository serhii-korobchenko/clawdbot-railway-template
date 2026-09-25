#!/usr/bin/env python3
"""PROROK schema v14: allow neutral direction for refresh candidate evidence."""
from __future__ import annotations
import argparse, os, sqlite3, sys
from pathlib import Path

DEFAULT_DB="/data/workspace/prorok/prorok.sqlite3"
TARGET_VERSION="14"

TABLE_SQL="""
CREATE TABLE refresh_candidate_evidence_v14 (
 candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
 refresh_event_result_id INTEGER NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
 direction TEXT NOT NULL CHECK(direction IN ('indicator','counterindicator','neutral')),
 strength TEXT CHECK(strength IS NULL OR strength IN ('weak','medium','strong')),
 relevance INTEGER CHECK(relevance IS NULL OR relevance BETWEEN 0 AND 100),
 credibility INTEGER CHECK(credibility IS NULL OR credibility BETWEEN 0 AND 100),
 title TEXT, source TEXT, url TEXT, published_at TEXT, summary TEXT, why_it_matters TEXT,
 duplicate_risk TEXT, freshness TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 validation_state TEXT NOT NULL DEFAULT 'legacy_unvalidated' CHECK(validation_state IN
 ('legacy_unvalidated','accepted','rejected_source_policy','rejected_date_conflict','rejected_invalid_metadata')),
 rejection_reason TEXT,
 FOREIGN KEY(refresh_event_result_id) REFERENCES refresh_event_results(refresh_event_result_id) ON DELETE CASCADE,
 UNIQUE(refresh_event_result_id,ordinal)
);
"""
INDEX_SQL="""
CREATE INDEX IF NOT EXISTS idx_refresh_candidate_result ON refresh_candidate_evidence(refresh_event_result_id,ordinal);
CREATE INDEX IF NOT EXISTS idx_refresh_candidate_validation ON refresh_candidate_evidence(validation_state,refresh_event_result_id,ordinal);
"""
COPY_COLUMNS=("candidate_id,refresh_event_result_id,ordinal,direction,strength,relevance,credibility,"
 "title,source,url,published_at,summary,why_it_matters,duplicate_risk,freshness,created_at,validation_state,rejection_reason")

def resolve_db(explicit):
 return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()
def version(c):
 r=c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone(); return None if r is None else str(r[0])
def table_exists(c,n):
 return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(n,)).fetchone() is not None
def validate(c):
 errors=[]
 if not table_exists(c,"refresh_candidate_evidence"): return ["missing table: refresh_candidate_evidence"]
 ddl=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='refresh_candidate_evidence'").fetchone()[0] or ""
 if "'neutral'" not in ddl: errors.append("refresh_candidate_evidence direction CHECK does not allow neutral")
 invalid=c.execute("SELECT COUNT(*) FROM refresh_candidate_evidence WHERE direction NOT IN ('indicator','counterindicator','neutral')").fetchone()[0]
 if invalid: errors.append(f"invalid candidate direction rows: {invalid}")
 if c.execute("PRAGMA foreign_key_check").fetchall(): errors.append("foreign_key_check failed")
 return errors

def apply_migration(c):
 if not table_exists(c,"refresh_candidate_evidence"): raise RuntimeError("refresh_candidate_evidence is missing")
 before=(c.execute("SELECT COUNT(*) FROM refresh_candidate_evidence").fetchone()[0],c.execute("SELECT MAX(candidate_id) FROM refresh_candidate_evidence").fetchone()[0])
 # SQLite cannot drop a referenced parent table with FK enforcement enabled. FK
 # enforcement is connection-scoped and must be toggled outside a transaction.
 c.execute("DROP TABLE IF EXISTS refresh_candidate_evidence_v14")
 c.execute(TABLE_SQL)
 c.execute(f"INSERT INTO refresh_candidate_evidence_v14({COPY_COLUMNS}) SELECT {COPY_COLUMNS} FROM refresh_candidate_evidence")
 c.execute("DROP TABLE refresh_candidate_evidence")
 c.execute("ALTER TABLE refresh_candidate_evidence_v14 RENAME TO refresh_candidate_evidence")
 c.executescript(INDEX_SQL)
 after=(c.execute("SELECT COUNT(*) FROM refresh_candidate_evidence").fetchone()[0],c.execute("SELECT MAX(candidate_id) FROM refresh_candidate_evidence").fetchone()[0])
 if before!=after: raise RuntimeError(f"candidate preservation check failed: before={before} after={after}")
 c.execute("""INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",(TARGET_VERSION,))

def main():
 p=argparse.ArgumentParser(description="Apply PROROK schema migration v13 -> v14 neutral candidate direction"); p.add_argument("--db"); p.add_argument("--check-only",action="store_true"); a=p.parse_args()
 db=resolve_db(a.db)
 if not db.exists(): print(f"ERROR: DB not found: {db}",file=sys.stderr); return 1
 c=sqlite3.connect(str(db),timeout=30)
 try:
  c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000"); before=version(c)
  if a.check_only:
   errors=validate(c)
   if before!=TARGET_VERSION: errors.append(f"schema_version expected 14, got {before!r}")
   if errors:
    for e in errors: print(f"ERROR: {e}")
    return 1
   print("OK: PROROK schema v14 verified"); return 0
  if before!="13": raise RuntimeError(f"schema_version expected 13 before migration, got {before!r}")
  # Disable enforcement only for the table-rebuild transaction, then validate
  # every FK before commit. This preserves child rows and their candidate ids.
  c.execute("PRAGMA foreign_keys=OFF")
  c.execute("BEGIN IMMEDIATE")
  apply_migration(c)
  fk_errors=c.execute("PRAGMA foreign_key_check").fetchall()
  if fk_errors: raise RuntimeError(f"foreign_key_check failed: {fk_errors[:5]}")
  errors=validate(c)
  if errors: raise RuntimeError("; ".join(errors))
  c.commit(); c.execute("PRAGMA foreign_keys=ON")
  print("OK: PROROK schema migration v14 applied"); print(f"schema_version_before: {before}"); print("schema_version_after: 14")
  print("candidate neutral direction: yes"); print("candidate ids/rows preserved: yes"); print("candidate promotion links preserved: yes"); print("official evidence/assessments changed: no"); print("foreign_key_check: ok"); return 0
 except Exception as e:
  c.rollback(); print(f"ERROR: {e}",file=sys.stderr); return 1
 finally: c.close()
if __name__=="__main__": raise SystemExit(main())
