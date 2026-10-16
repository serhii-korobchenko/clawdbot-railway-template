from __future__ import annotations
import sqlite3
from pathlib import Path
from unittest.mock import patch
from prorok.prorok_refresh_notifier import notify_completed_telegram_refreshes


def make_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
    CREATE TABLE refresh_runs(
      refresh_id INTEGER PRIMARY KEY, trigger_source TEXT, phase TEXT, status TEXT,
      target_count INTEGER, events_checked INTEGER, events_with_new_evidence INTEGER,
      new_evidence_count INTEGER, recommendations_count INTEGER, no_change_count INTEGER, error_count INTEGER,
      finished_at TEXT
    );
    CREATE TABLE refresh_notifications(
      notification_id INTEGER PRIMARY KEY AUTOINCREMENT, refresh_id INTEGER NOT NULL,
      notification_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      last_attempt_at TEXT, sent_at TEXT, last_error TEXT,
      UNIQUE(refresh_id,notification_type)
    );
    INSERT INTO refresh_runs VALUES(45,'telegram','done','partial',7,5,1,2,0,5,2,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
    INSERT INTO refresh_runs VALUES(46,'scheduled','done','completed',7,7,0,0,0,7,0,datetime('now','-2 days'));
    INSERT INTO refresh_runs VALUES(47,'scheduled','done','partial',7,5,1,2,0,5,2,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
    """
    )
    conn.commit()
    conn.close()


def test_notifier_sends_telegram_and_latest_scheduled_batch_once(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    make_db(db)
    with patch("prorok.prorok_refresh_notifier._scheduled_message", return_value="FINAL DAILY STATUS"), patch(
        "prorok.prorok_refresh_notifier.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        first = notify_completed_telegram_refreshes(db)
        second = notify_completed_telegram_refreshes(db)

    assert first["sent"] == 2
    assert second["sent"] == 0
    assert run.call_count == 2

    messages = [call.args[0][call.args[0].index("--message") + 1] for call in run.call_args_list]
    assert any("Refresh #45" in message for message in messages)
    assert "FINAL DAILY STATUS" in messages

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT refresh_id,status,attempts FROM refresh_notifications ORDER BY refresh_id"
    ).fetchall()
    assert rows == [(45, "sent", 1), (47, "sent", 1)]
    assert conn.execute(
        "SELECT COUNT(*) FROM refresh_notifications WHERE refresh_id=46"
    ).fetchone()[0] == 0
    conn.close()


def test_notifier_does_not_send_scheduled_batch_while_running(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE refresh_runs SET phase='collecting', status='running', finished_at=NULL WHERE refresh_id=47")
    conn.commit()
    conn.close()

    with patch("prorok.prorok_refresh_notifier._scheduled_message") as scheduled_message, patch(
        "prorok.prorok_refresh_notifier.subprocess.run"
    ) as run:
        run.return_value.returncode = 0
        result = notify_completed_telegram_refreshes(db)

    assert result["sent"] == 1  # interactive refresh #45 only
    scheduled_message.assert_not_called()
    assert run.call_count == 1


def test_notifier_retries_failed_delivery(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM refresh_runs WHERE refresh_id IN (46,47)")
    conn.commit()
    conn.close()

    with patch(
        "prorok.prorok_refresh_notifier.subprocess.run",
        side_effect=[RuntimeError("boom"), None],
    ):
        first = notify_completed_telegram_refreshes(db)
        second = notify_completed_telegram_refreshes(db)
    assert first["failed"] == 1
    assert second["sent"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status,attempts FROM refresh_notifications").fetchone() == (
        "sent",
        2,
    )
    conn.close()


def test_notifier_recovers_stale_sending_after_process_crash(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM refresh_runs WHERE refresh_id IN (46,47)")
    conn.execute(
        """INSERT INTO refresh_notifications(
               refresh_id,notification_type,status,attempts,last_attempt_at
           ) VALUES(45,'telegram_completion','sending',1,'2026-01-01T00:00:00.000Z')"""
    )
    conn.commit()
    conn.close()
    with patch("prorok.prorok_refresh_notifier.subprocess.run") as run:
        run.return_value.returncode = 0
        result = notify_completed_telegram_refreshes(db)
    assert result["sent"] == 1
    assert run.call_count == 1
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT status,attempts FROM refresh_notifications WHERE refresh_id=45"
    ).fetchone()
    assert row == ("sent", 2)
    conn.close()


def test_notifier_does_not_duplicate_fresh_sending(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    make_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM refresh_runs WHERE refresh_id IN (46,47)")
    conn.execute(
        """INSERT INTO refresh_notifications(
               refresh_id,notification_type,status,attempts,last_attempt_at
           ) VALUES(
               45,'telegram_completion','sending',1,
               strftime('%Y-%m-%dT%H:%M:%fZ','now')
           )"""
    )
    conn.commit()
    conn.close()
    with patch("prorok.prorok_refresh_notifier.subprocess.run") as run:
        result = notify_completed_telegram_refreshes(db)
    assert result["sent"] == 0
    assert run.call_count == 0
