from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


def load_migration_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "prorok"
        / "migrations"
        / "006_deletion_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prorok_migration_006_deletion_audit",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v5_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );

        INSERT INTO meta(key, value)
        VALUES('schema_version', '5');

        CREATE TABLE refresh_user_decisions(
            decision_id INTEGER PRIMARY KEY
        );

        INSERT INTO refresh_user_decisions(decision_id)
        VALUES(1);
        """
    )
    conn.commit()
    conn.close()


def test_migration_v6_creates_snapshot_only_deletion_audit(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v5_db(db)
    migration = load_migration_module()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    migration.apply_migration(conn)
    conn.commit()

    assert migration.schema_version(conn) == "6"
    assert migration.validate(conn) == []

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(deletion_audit)")
    }
    assert {
        "deletion_id",
        "target_type",
        "target_id_snapshot",
        "event_id_snapshot",
        "target_label_snapshot",
        "source_id_snapshot",
        "assessment_count",
        "evidence_count",
        "decision_source",
        "actor_snapshot",
        "deleted_at",
    } <= columns

    assert conn.execute(
        "PRAGMA foreign_key_list(deletion_audit)"
    ).fetchall() == []

    conn.execute(
        """
        INSERT INTO deletion_audit(
            target_type,
            target_id_snapshot,
            event_id_snapshot,
            target_label_snapshot,
            assessment_count,
            evidence_count,
            decision_source,
            actor_snapshot
        )
        VALUES(
            'event',
            'event_a',
            'event_a',
            'Event A',
            3,
            4,
            'telegram',
            'telegram:123'
        )
        """
    )
    conn.commit()

    row = conn.execute(
        """
        SELECT *
        FROM deletion_audit
        WHERE deletion_id = 1
        """
    ).fetchone()

    assert row["target_type"] == "event"
    assert row["target_id_snapshot"] == "event_a"
    assert row["assessment_count"] == 3
    assert row["evidence_count"] == 4
    assert row["decision_source"] == "telegram"
    assert row["actor_snapshot"] == "telegram:123"
    assert row["deleted_at"]

    # Existing v5 data is preserved.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM refresh_user_decisions"
    ).fetchone()["n"] == 1

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO deletion_audit(
                target_type,
                target_id_snapshot
            )
            VALUES('invalid', 'x')
            """
        )

    conn.close()


def test_migration_v6_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v5_db(db)
    migration = load_migration_module()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    migration.apply_migration(conn)
    migration.apply_migration(conn)
    conn.commit()

    assert migration.schema_version(conn) == "6"
    assert migration.validate(conn) == []

    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(deletion_audit)")
    }
    assert "idx_deletion_audit_event" in indexes
    assert "idx_deletion_audit_target" in indexes

    conn.close()
