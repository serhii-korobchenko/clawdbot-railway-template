import importlib.util
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "prorok" / "migrations" / "017_prorok_app_tracking.py"
spec = importlib.util.spec_from_file_location("migration_017", MODULE_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_migration_v17_preserves_evidence_and_tracks_reversible_status(tmp_path):
    db = tmp_path / "prorok.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
        INSERT INTO meta(key,value) VALUES('schema_version','16');
        CREATE TABLE evidence_items (evidence_id INTEGER PRIMARY KEY, event_id TEXT NOT NULL);
        INSERT INTO evidence_items(evidence_id,event_id) VALUES(19,'event-1');
    """)
    before = conn.execute("SELECT * FROM evidence_items").fetchall()
    conn.execute("BEGIN IMMEDIATE")
    migration.migrate(conn)
    errors = migration.validate(conn)
    conn.commit()

    assert errors == []
    assert migration.version(conn) == "17"
    assert conn.execute("SELECT * FROM evidence_items").fetchall() == before

    conn.execute("INSERT INTO evidence_prorok_app_status_history(evidence_id,state,source,actor) VALUES(19,'marked','telegram','tester')")
    conn.execute("INSERT INTO evidence_prorok_app_status_history(evidence_id,state,source,actor) VALUES(19,'unmarked','telegram','tester')")
    rows = conn.execute("SELECT state FROM evidence_prorok_app_status_history WHERE evidence_id=19 ORDER BY prorok_app_status_history_id").fetchall()
    assert rows == [('marked',), ('unmarked',)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
