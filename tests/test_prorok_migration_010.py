from __future__ import annotations
import importlib.util, sqlite3
from pathlib import Path

def load_migration():
    path=Path(__file__).resolve().parent.parent/"prorok"/"migrations"/"010_refresh_notifications.py"
    spec=importlib.util.spec_from_file_location("prorok_migration_010",path)
    mod=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod); return mod

def make_v9(path: Path):
    conn=sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT);
    INSERT INTO meta(key,value) VALUES('schema_version','9');
    CREATE TABLE refresh_runs(refresh_id INTEGER PRIMARY KEY);
    INSERT INTO refresh_runs(refresh_id) VALUES(1);
    """); conn.commit(); conn.close()

def test_migration_v10(tmp_path: Path):
    db=tmp_path/"db.sqlite3"; make_v9(db); m=load_migration()
    conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON")
    m.apply_migration(conn); conn.commit()
    assert m.schema_version(conn)=="10"; assert m.validate(conn)==[]
    conn.execute("INSERT INTO refresh_notifications(refresh_id,notification_type) VALUES(1,'telegram_completion')")
    conn.commit()
    row=conn.execute("SELECT status,attempts FROM refresh_notifications").fetchone()
    assert dict(row)=={"status":"pending","attempts":0}
    conn.close()
