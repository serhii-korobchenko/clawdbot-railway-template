import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "prorok" / "migrations" / "014_candidate_neutral_direction.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("migration014", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v13_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        INSERT INTO meta(key,value) VALUES('schema_version','13');
        CREATE TABLE events(event_id TEXT PRIMARY KEY);
        CREATE TABLE refresh_runs(refresh_id INTEGER PRIMARY KEY);
        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY,
            refresh_id INTEGER NOT NULL,
            event_id TEXT,
            FOREIGN KEY(refresh_id) REFERENCES refresh_runs(refresh_id),
            FOREIGN KEY(event_id) REFERENCES events(event_id)
        );
        CREATE TABLE refresh_candidate_evidence (
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_event_result_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
            direction TEXT NOT NULL CHECK(direction IN ('indicator', 'counterindicator')),
            strength TEXT CHECK(strength IS NULL OR strength IN ('weak', 'medium', 'strong')),
            relevance INTEGER CHECK(relevance IS NULL OR relevance BETWEEN 0 AND 100),
            credibility INTEGER CHECK(credibility IS NULL OR credibility BETWEEN 0 AND 100),
            title TEXT, source TEXT, url TEXT, published_at TEXT, summary TEXT,
            why_it_matters TEXT, duplicate_risk TEXT, freshness TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            validation_state TEXT NOT NULL DEFAULT 'legacy_unvalidated'
                CHECK(validation_state IN (
                    'legacy_unvalidated','accepted','rejected_source_policy',
                    'rejected_date_conflict','rejected_invalid_metadata'
                )),
            rejection_reason TEXT,
            FOREIGN KEY(refresh_event_result_id)
                REFERENCES refresh_event_results(refresh_event_result_id) ON DELETE CASCADE,
            UNIQUE(refresh_event_result_id, ordinal)
        );
        INSERT INTO refresh_runs(refresh_id) VALUES(1);
        INSERT INTO events(event_id) VALUES('event_a');
        INSERT INTO refresh_event_results(refresh_event_result_id,refresh_id,event_id)
        VALUES(10,1,'event_a');
        INSERT INTO refresh_candidate_evidence(
            candidate_id,refresh_event_result_id,ordinal,direction,strength,relevance,
            credibility,url,validation_state
        ) VALUES(7,10,1,'indicator','medium',80,90,'https://example.com/a','accepted');
        """
    )
    conn.commit()
    return conn


def test_v14_preserves_rows_and_allows_neutral(tmp_path):
    migration = load_migration()
    conn = make_v13_db(tmp_path / "p.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        migration.apply_migration(conn)
        assert migration.validate(conn) == []
        conn.commit()

        row = conn.execute(
            "SELECT candidate_id,direction,url,validation_state FROM refresh_candidate_evidence"
        ).fetchone()
        assert dict(row) == {
            "candidate_id": 7,
            "direction": "indicator",
            "url": "https://example.com/a",
            "validation_state": "accepted",
        }
        assert migration.version(conn) == "14"

        conn.execute(
            "INSERT INTO refresh_candidate_evidence(refresh_event_result_id,ordinal,direction,url,validation_state) "
            "VALUES(10,2,'neutral','https://example.com/b','accepted')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT direction FROM refresh_candidate_evidence WHERE ordinal=2"
        ).fetchone()[0] == "neutral"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_v13_rejects_neutral_before_migration(tmp_path):
    conn = make_v13_db(tmp_path / "p.sqlite3")
    try:
        try:
            conn.execute(
                "INSERT INTO refresh_candidate_evidence(refresh_event_result_id,ordinal,direction,url) "
                "VALUES(10,2,'neutral','https://example.com/b')"
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("v13 schema unexpectedly accepted neutral direction")
    finally:
        conn.close()
