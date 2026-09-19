from __future__ import annotations

import importlib.util
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest


def load_cli():
    path = (
        Path(__file__).resolve().parent.parent
        / "prorok"
        / "prorok_event_status_cli.py"
    )
    spec = importlib.util.spec_from_file_location("prorok_event_status_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_v9_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );
        INSERT INTO meta(key, value) VALUES('schema_version', '9');

        CREATE TABLE events(
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE event_status_audit (
            status_change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id_snapshot TEXT NOT NULL,
            title_snapshot TEXT,
            from_status TEXT NOT NULL
                CHECK(from_status IN ('active', 'paused', 'resolved', 'archived')),
            to_status TEXT NOT NULL
                CHECK(to_status IN ('active', 'paused', 'resolved', 'archived')),
            decision_source TEXT NOT NULL DEFAULT 'telegram',
            actor_snapshot TEXT,
            changed_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        INSERT INTO events(event_id, title, status, updated_at, archived_at)
        VALUES
            ('active_event', 'Active event', 'active', '2026-09-01T00:00:00Z', NULL),
            ('paused_event', 'Paused event', 'paused', '2026-09-01T00:00:00Z', NULL),
            ('archived_event', 'Archived event', 'archived', '2026-09-01T00:00:00Z', '2026-09-02T00:00:00Z'),
            ('resolved_event', 'Resolved event', 'resolved', '2026-09-01T00:00:00Z', NULL);
        """
    )
    conn.commit()
    conn.close()


def args(db: Path, event_id: str, from_status: str, to_status: str) -> Namespace:
    return Namespace(
        db=str(db),
        event_id=event_id,
        from_status=from_status,
        to_status=to_status,
        source="telegram",
        actor="telegram:123",
    )


def read_event(db: Path, event_id: str):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    conn.close()
    return row


def audit_count(db: Path, event_id: str) -> int:
    conn = sqlite3.connect(db)
    value = conn.execute(
        "SELECT COUNT(*) FROM event_status_audit WHERE event_id_snapshot = ?",
        (event_id,),
    ).fetchone()[0]
    conn.close()
    return value


@pytest.mark.parametrize(
    ("event_id", "from_status", "to_status"),
    [
        ("active_event", "active", "paused"),
        ("paused_event", "paused", "active"),
        ("active_event", "active", "archived"),
        ("paused_event", "paused", "archived"),
        ("archived_event", "archived", "active"),
    ],
)
def test_allowed_transitions_write_one_audit(
    tmp_path: Path,
    event_id: str,
    from_status: str,
    to_status: str,
) -> None:
    db = tmp_path / "db.sqlite3"
    make_v9_db(db)
    cli = load_cli()

    assert cli.cmd_set_status(args(db, event_id, from_status, to_status)) == 0

    event = read_event(db, event_id)
    assert event["status"] == to_status
    assert event["updated_at"] != "2026-09-01T00:00:00Z"
    if to_status == "archived":
        assert event["archived_at"] is not None
    if from_status == "archived" and to_status == "active":
        assert event["archived_at"] is None

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    audit = conn.execute(
        "SELECT * FROM event_status_audit WHERE event_id_snapshot = ?",
        (event_id,),
    ).fetchone()
    assert audit["from_status"] == from_status
    assert audit["to_status"] == to_status
    assert audit["decision_source"] == "telegram"
    assert audit["actor_snapshot"] == "telegram:123"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_stale_transition_does_not_write(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v9_db(db)
    cli = load_cli()

    with pytest.raises(cli.CliError, match="stale status transition"):
        cli.cmd_set_status(args(db, "paused_event", "active", "archived"))

    assert read_event(db, "paused_event")["status"] == "paused"
    assert audit_count(db, "paused_event") == 0


def test_replay_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v9_db(db)
    cli = load_cli()
    action = args(db, "active_event", "active", "paused")

    assert cli.cmd_set_status(action) == 0
    assert cli.cmd_set_status(action) == 0
    assert read_event(db, "active_event")["status"] == "paused"
    assert audit_count(db, "active_event") == 1


def test_resolved_transition_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v9_db(db)
    cli = load_cli()

    with pytest.raises(cli.CliError, match="unsupported status transition"):
        cli.cmd_set_status(args(db, "resolved_event", "resolved", "active"))

    assert read_event(db, "resolved_event")["status"] == "resolved"
    assert audit_count(db, "resolved_event") == 0


def test_failure_after_update_rolls_back_audit_and_status(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v9_db(db)
    cli = load_cli()

    def fail_integrity(_conn):
        raise cli.CliError("forced integrity failure")

    cli.validate_integrity = fail_integrity

    with pytest.raises(cli.CliError, match="forced integrity failure"):
        cli.cmd_set_status(args(db, "active_event", "active", "paused"))

    assert read_event(db, "active_event")["status"] == "active"
    assert audit_count(db, "active_event") == 0


def test_requires_schema_v9(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    make_v9_db(db)
    cli = load_cli()

    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    with pytest.raises(cli.CliError, match="schema v9"):
        cli.cmd_set_status(args(db, "active_event", "active", "paused"))

    assert read_event(db, "active_event")["status"] == "active"
    assert audit_count(db, "active_event") == 0
