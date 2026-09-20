from __future__ import annotations
import sqlite3
from pathlib import Path
from unittest.mock import patch
from prorok.prorok_refresh_notifier import notify_completed_telegram_refreshes

def make_db(path: Path):
    conn=sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE refresh_runs(
      refresh_id INTEGER PRIMARY KEY, trigger_source TEXT, phase TEXT, status TEXT,
      target_count INTEGER, events_checked INTEGER, events_with_new_evidence INTEGER,
      new_evidence_count INTEGER, recommendations_count INTEGER, no_change_count INTEGER, error_count INTEGER
    );
    CREATE TABLE refresh_notifications(
      notification_id INTEGER PRIMARY KEY AUTOINCREMENT, refresh_id INTEGER NOT NULL,
      notification_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      last_attempt_at TEXT, sent_at TEXT, last_error TEXT,
      UNIQUE(refresh_id,notification_type)
    );
    INSERT INTO refresh_runs VALUES(45,'telegram','done','partial',7,5,1,2,0,5,2);
    INSERT INTO refresh_runs VALUES(46,'scheduled','done','completed',7,7,0,0,0,7,0);
    """); conn.commit(); conn.close()

def test_notifier_sends_telegram_batch_once(tmp_path: Path):
    db=tmp_path/"db.sqlite3"; make_db(db)
    with patch("prorok.prorok_refresh_notifier.subprocess.run") as run:
        run.return_value.returncode=0
        first=notify_completed_telegram_refreshes(db)
        second=notify_completed_telegram_refreshes(db)
    assert first["sent"]==1
    assert second["sent"]==0
    assert run.call_count==1
    args=run.call_args.args[0]
    assert args[:5]==["openclaw","message","send","--channel","telegram"]
    assert "Refresh #45" in args[args.index("--message")+1]
    conn=sqlite3.connect(db)
    row=conn.execute("SELECT status,attempts FROM refresh_notifications WHERE refresh_id=45").fetchone()
    assert row==("sent",1)
    assert conn.execute("SELECT COUNT(*) FROM refresh_notifications WHERE refresh_id=46").fetchone()[0]==0
    conn.close()

def test_notifier_retries_failed_delivery(tmp_path: Path):
    db=tmp_path/"db.sqlite3"; make_db(db)
    with patch("prorok.prorok_refresh_notifier.subprocess.run", side_effect=[RuntimeError("boom"), None]):
        first=notify_completed_telegram_refreshes(db)
        second=notify_completed_telegram_refreshes(db)
    assert first["failed"]==1
    assert second["sent"]==1
    conn=sqlite3.connect(db)
    assert conn.execute("SELECT status,attempts FROM refresh_notifications").fetchone()==("sent",2)
    conn.close()
