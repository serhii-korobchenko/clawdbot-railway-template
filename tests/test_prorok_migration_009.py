from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


def load_migration():
    path = (
        Path(__file__).resolve().parent.parent
        / "prorok"
        / "migrations"
        / "009_event_status_audit.py"
    )
    spec = importlib.util.spec_from_file_location("prorok_migration_009", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v8_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );
        INSERT INTO meta(key, value) VALUES('schema_version', '8');

        CREATE TABLE events(
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        INSERT INTO events(event_id, title, status, updated_at)
        VALUES('event_a', 'Event A', 'active', '2026-09-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()


def test_migration_v9_creates_snapshot_status_audit(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v8_db(db)
    migration = load_migration()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    migration.apply_migration(conn)
    conn.commit()

    assert migration.schema_version(conn) == "9"
    assert migration.validate(conn) == []

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(event_status_audit)")
    }
    assert {
        "status_change_id",
        "event_id_snapshot",
        "title_snapshot",
        "from_status",
        "to_status",
        "decision_source",
        "actor_snapshot",
        "changed_at",
    } <= columns
    assert conn.execute(
        "PRAGMA foreign_key_list(event_status_audit)"
    ).fetchall() == []

    conn.execute(
        """
        INSERT INTO event_status_audit(
            event_id_snapshot, title_snapshot, from_status, to_status,
            decision_source, actor_snapshot
        ) VALUES('event_a', 'Event A', 'active', 'archived', 'telegram', 'telegram:123')
        """
    )
    conn.execute("DELETE FROM events WHERE event_id='event_a'")
    conn.commit()

    row = conn.execute("SELECT * FROM event_status_audit").fetchone()
    assert row["event_id_snapshot"] == "event_a"
    assert row["from_status"] == "active"
    assert row["to_status"] == "archived"
    assert row["decision_source"] == "telegram"
    assert row["actor_snapshot"] == "telegram:123"
    assert row["changed_at"]

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO event_status_audit(
                event_id_snapshot, from_status, to_status
            ) VALUES('x', 'invalid', 'active')
            """
        )
    conn.close()


def test_migration_v9_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v8_db(db)
    migration = load_migration()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    migration.apply_migration(conn)
    migration.apply_migration(conn)
    conn.commit()

    assert migration.schema_version(conn) == "9"
    assert migration.validate(conn) == []
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(event_status_audit)")
    }
    assert "idx_event_status_audit_event" in indexes
    conn.close()
