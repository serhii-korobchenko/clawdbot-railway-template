import importlib.util
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/"prorok"/"migrations"/"014_candidate_neutral_direction.py"

def load_migration():
 spec=importlib.util.spec_from_file_location("migration014",MIGRATION); module=importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module); return module

def make_v13_db(path):
 c=sqlite3.connect(path); c.execute("PRAGMA foreign_keys=ON")
 c.executescript("""
 CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT); INSERT INTO meta(key,value) VALUES('schema_version','13');
 CREATE TABLE events(event_id TEXT PRIMARY KEY); CREATE TABLE refresh_runs(refresh_id INTEGER PRIMARY KEY);
 CREATE TABLE refresh_event_results(refresh_event_result_id INTEGER PRIMARY KEY,refresh_id INTEGER NOT NULL,event_id TEXT,FOREIGN KEY(refresh_id) REFERENCES refresh_runs(refresh_id),FOREIGN KEY(event_id) REFERENCES events(event_id));
 CREATE TABLE refresh_candidate_evidence(candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,refresh_event_result_id INTEGER NOT NULL,ordinal INTEGER NOT NULL CHECK(ordinal>=1),direction TEXT NOT NULL CHECK(direction IN ('indicator','counterindicator')),strength TEXT CHECK(strength IS NULL OR strength IN ('weak','medium','strong')),relevance INTEGER CHECK(relevance IS NULL OR relevance BETWEEN 0 AND 100),credibility INTEGER CHECK(credibility IS NULL OR credibility BETWEEN 0 AND 100),title TEXT,source TEXT,url TEXT,published_at TEXT,summary TEXT,why_it_matters TEXT,duplicate_risk TEXT,freshness TEXT,created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),validation_state TEXT NOT NULL DEFAULT 'legacy_unvalidated' CHECK(validation_state IN ('legacy_unvalidated','accepted','rejected_source_policy','rejected_date_conflict','rejected_invalid_metadata')),rejection_reason TEXT,FOREIGN KEY(refresh_event_result_id) REFERENCES refresh_event_results(refresh_event_result_id) ON DELETE CASCADE,UNIQUE(refresh_event_result_id,ordinal));
 CREATE TABLE refresh_candidate_promotions(promotion_id INTEGER PRIMARY KEY,candidate_id INTEGER NOT NULL UNIQUE,evidence_id INTEGER NOT NULL,FOREIGN KEY(candidate_id) REFERENCES refresh_candidate_evidence(candidate_id) ON DELETE RESTRICT);
 INSERT INTO refresh_runs VALUES(1); INSERT INTO events VALUES('event_a'); INSERT INTO refresh_event_results VALUES(10,1,'event_a');
 INSERT INTO refresh_candidate_evidence(candidate_id,refresh_event_result_id,ordinal,direction,strength,relevance,credibility,url,validation_state) VALUES(7,10,1,'indicator','medium',80,90,'https://example.com/a','accepted');
 INSERT INTO refresh_candidate_promotions VALUES(3,7,99);
 """); c.commit(); return c

def run_apply_like_main(c,migration):
 c.execute("PRAGMA foreign_keys=OFF"); c.execute("BEGIN IMMEDIATE"); migration.apply_migration(c)
 assert c.execute("PRAGMA foreign_key_check").fetchall()==[]; assert migration.validate(c)==[]; c.commit(); c.execute("PRAGMA foreign_keys=ON")

def test_v14_preserves_rows_promotion_links_and_allows_neutral(tmp_path):
 m=load_migration(); c=make_v13_db(tmp_path/"p.sqlite3"); c.row_factory=sqlite3.Row
 try:
  run_apply_like_main(c,m)
  row=c.execute("SELECT candidate_id,direction,url,validation_state FROM refresh_candidate_evidence").fetchone()
  assert dict(row)=={"candidate_id":7,"direction":"indicator","url":"https://example.com/a","validation_state":"accepted"}
  assert c.execute("SELECT candidate_id,evidence_id FROM refresh_candidate_promotions").fetchone()==(7,99)
  assert m.version(c)=="14"
  c.execute("INSERT INTO refresh_candidate_evidence(refresh_event_result_id,ordinal,direction,url,validation_state) VALUES(10,2,'neutral','https://example.com/b','accepted')"); c.commit()
  assert c.execute("SELECT direction FROM refresh_candidate_evidence WHERE ordinal=2").fetchone()[0]=="neutral"
  assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
 finally: c.close()

def test_v13_rejects_neutral_before_migration(tmp_path):
 c=make_v13_db(tmp_path/"p.sqlite3")
 try:
  try: c.execute("INSERT INTO refresh_candidate_evidence(refresh_event_result_id,ordinal,direction,url) VALUES(10,2,'neutral','https://example.com/b')")
  except sqlite3.IntegrityError: pass
  else: raise AssertionError("v13 schema unexpectedly accepted neutral direction")
 finally: c.close()
