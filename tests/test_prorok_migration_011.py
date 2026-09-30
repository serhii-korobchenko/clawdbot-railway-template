from __future__ import annotations
import importlib.util, sqlite3
from pathlib import Path

def load_migration():
    path=Path(__file__).resolve().parent.parent/"prorok"/"migrations"/"011_refresh_calibration.py"
    spec=importlib.util.spec_from_file_location("prorok_migration_011",path)
    mod=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod); return mod

def make_v10(path: Path):
    conn=sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT);
    INSERT INTO meta(key,value) VALUES('schema_version','10');
    CREATE TABLE refresh_event_results(refresh_event_result_id INTEGER PRIMARY KEY);
    INSERT INTO refresh_event_results(refresh_event_result_id) VALUES(1);
    """); conn.commit(); conn.close()

def test_migration_v11(tmp_path: Path):
    db=tmp_path/"db.sqlite3"; make_v10(db); m=load_migration()
    conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON")
    m.apply_migration(conn); conn.commit()
    assert m.schema_version(conn)=="11"; assert m.validate(conn)==[]
    row=conn.execute("SELECT probability_delta,net_evidence_direction,net_evidence_impact,baseline_incorporation,category_transition,delta_justification FROM refresh_event_results").fetchone()
    assert all(value is None for value in row)
    conn.execute("""UPDATE refresh_event_results SET probability_delta=5,net_evidence_direction='positive',net_evidence_impact='weak',baseline_incorporation='high',category_transition=0,delta_justification='test' WHERE refresh_event_result_id=1""")
    conn.commit(); assert m.validate(conn)==[]
    conn.close()
