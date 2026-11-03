from __future__ import annotations

import importlib.util
import sqlite3
from argparse import Namespace
from pathlib import Path


def load_cli_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "prorok"
        / "prorok_delete_cli.py"
    )
    spec = importlib.util.spec_from_file_location("prorok_delete_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v6_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );
        INSERT INTO meta(key, value) VALUES('schema_version', '6');

        CREATE TABLE events(
            event_id TEXT PRIMARY KEY,
            title TEXT
        );

        CREATE TABLE runs(
            run_id INTEGER PRIMARY KEY
        );

        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            run_id INTEGER,
            probability_percent INTEGER,
            FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
        );

        CREATE TABLE sources(
            source_id INTEGER PRIMARY KEY,
            title TEXT
        );

        CREATE TABLE evidence_items(
            evidence_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            run_id INTEGER,
            direction TEXT,
            strength TEXT,
            summary TEXT,
            FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
        );

        CREATE TABLE deletion_audit (
            deletion_id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL CHECK(target_type IN ('event', 'evidence')),
            target_id_snapshot TEXT NOT NULL,
            event_id_snapshot TEXT,
            target_label_snapshot TEXT,
            source_id_snapshot INTEGER,
            assessment_count INTEGER NOT NULL DEFAULT 0 CHECK(assessment_count >= 0),
            evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(evidence_count >= 0),
            decision_source TEXT NOT NULL DEFAULT 'telegram',
            actor_snapshot TEXT,
            deleted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        INSERT INTO events(event_id, title) VALUES
            ('event_a', 'Event A'),
            ('event_b', 'Event B');

        INSERT INTO assessments(assessment_id, event_id, probability_percent) VALUES
            (1, 'event_a', 40),
            (2, 'event_a', 50),
            (3, 'event_b', 20);

        INSERT INTO sources(source_id, title) VALUES
            (10, 'Shared source'),
            (11, 'Only event A');

        INSERT INTO evidence_items(
            evidence_id, event_id, source_id, direction, strength, summary
        ) VALUES
            (100, 'event_a', 10, 'indicator', 'medium', 'Evidence A1'),
            (101, 'event_a', 11, 'counterindicator', 'weak', 'Evidence A2'),
            (102, 'event_b', 10, 'indicator', 'strong', 'Evidence B1');
        """
    )
    conn.commit()
    conn.close()


def args_for(db: Path, **kwargs):
    return Namespace(
        db=str(db),
        home=None,
        source=kwargs.get("source", "telegram"),
        actor=kwargs.get("actor", "telegram:123"),
        event_id=kwargs.get("event_id"),
        evidence_id=kwargs.get("evidence_id"),
    )


def test_delete_evidence_preserves_source_and_writes_audit(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v6_db(db)
    cli = load_cli_module()

    rc = cli.cmd_delete_evidence(args_for(db, evidence_id=100))
    assert rc == 0

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    assert conn.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE evidence_id=100"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE source_id=10"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE evidence_id=102"
    ).fetchone()[0] == 1

    audit = conn.execute(
        "SELECT * FROM deletion_audit WHERE target_type='evidence'"
    ).fetchone()
    assert audit["target_id_snapshot"] == "100"
    assert audit["event_id_snapshot"] == "event_a"
    assert audit["target_label_snapshot"] == "Evidence A1"
    assert audit["source_id_snapshot"] == 10
    assert audit["assessment_count"] == 0
    assert audit["evidence_count"] == 1
    assert audit["decision_source"] == "telegram"
    assert audit["actor_snapshot"] == "telegram:123"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_delete_evidence_replay_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v6_db(db)
    cli = load_cli_module()

    args = args_for(db, evidence_id=100)
    assert cli.cmd_delete_evidence(args) == 0
    assert cli.cmd_delete_evidence(args) == 0

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM deletion_audit WHERE target_type='evidence' AND target_id_snapshot='100'"
    ).fetchone()[0] == 1
    conn.close()


def test_delete_event_cascades_children_preserves_sources_and_writes_audit(
    tmp_path: Path,
) -> None:
    db = tmp_path / "db.sqlite3"
    make_v6_db(db)
    cli = load_cli_module()

    rc = cli.cmd_delete_event(args_for(db, event_id="event_a"))
    assert rc == 0

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_id='event_a'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM assessments WHERE event_id='event_a'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE event_id='event_a'"
    ).fetchone()[0] == 0

    # Shared and event-only source rows survive event deletion.
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE event_id='event_b' AND source_id=10"
    ).fetchone()[0] == 1

    audit = conn.execute(
        "SELECT * FROM deletion_audit WHERE target_type='event'"
    ).fetchone()
    assert audit["target_id_snapshot"] == "event_a"
    assert audit["event_id_snapshot"] == "event_a"
    assert audit["target_label_snapshot"] == "Event A"
    assert audit["source_id_snapshot"] is None
    assert audit["assessment_count"] == 2
    assert audit["evidence_count"] == 2
    assert audit["decision_source"] == "telegram"
    assert audit["actor_snapshot"] == "telegram:123"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_delete_event_replay_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v6_db(db)
    cli = load_cli_module()

    args = args_for(db, event_id="event_a")
    assert cli.cmd_delete_event(args) == 0
    assert cli.cmd_delete_event(args) == 0

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM deletion_audit WHERE target_type='event' AND target_id_snapshot='event_a'"
    ).fetchone()[0] == 1
    conn.close()


def test_delete_requires_schema_v6(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v6_db(db)
    cli = load_cli_module()

    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    try:
        cli.cmd_delete_evidence(args_for(db, evidence_id=100))
        raise AssertionError("expected CliError")
    except cli.CliError as exc:
        assert "schema v6 required" in str(exc)

    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence_items WHERE evidence_id=100"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM deletion_audit").fetchone()[0] == 0
    conn.close()
