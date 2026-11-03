#!/usr/bin/env python3
"""Send once-only completion notices for Telegram and scheduled PROROK refreshes."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

DEFAULT_DB = Path("/data/workspace/prorok/prorok.sqlite3")
DEFAULT_CHAT_ID = "-1003804919781"
DEFAULT_THREAD_ID = "112"
TYPE = "telegram_completion"
SENDING_STALE_SECONDS = 120
SCHEDULED_MAX_AGE_HOURS = 24


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _telegram_message(row: sqlite3.Row) -> str:
    status = {
        "completed": "✅ Завершено",
        "partial": "⚠️ Частково завершено",
        "failed": "❌ Помилка",
    }.get(row["status"], row["status"])
    return "\n".join(
        [
            "🔄 PROROK · Перевірку завершено",
            f"Refresh #{row['refresh_id']} · {status}",
            f"Перевірено: {row['events_checked']} із {row['target_count']}",
            f"Подій з новими evidence: {row['events_with_new_evidence']}",
            f"Нових evidence: {row['new_evidence_count']}",
            f"Рекомендацій: {row['recommendations_count']}",
            f"Без зміни прогнозу: {row['no_change_count']}",
            f"Помилок: {row['error_count']}",
        ]
    )


def _scheduled_message() -> str:
    """Render the existing morning summary after the scheduled batch is terminal."""
    script = Path(__file__).with_name("prorok_daily_status.sh")
    result = subprocess.run(
        ["bash", str(script)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    message = (result.stdout or "").strip()
    if not message:
        raise RuntimeError("scheduled PROROK daily status produced empty output")
    return message


def _claim(conn: sqlite3.Connection, refresh_id: int) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """INSERT INTO refresh_notifications(refresh_id,notification_type,status)
           VALUES(?,?, 'pending')
           ON CONFLICT(refresh_id,notification_type) DO NOTHING""",
        (refresh_id, TYPE),
    )
    row = conn.execute(
        "SELECT status FROM refresh_notifications WHERE refresh_id=? AND notification_type=?",
        (refresh_id, TYPE),
    ).fetchone()
    if row is None or row["status"] == "sent":
        conn.commit()
        return False
    if row["status"] == "sending":
        stale = conn.execute(
            """SELECT 1 FROM refresh_notifications
               WHERE refresh_id=? AND notification_type=?
                 AND last_attempt_at IS NOT NULL
                 AND julianday(last_attempt_at) <= julianday('now') - (? / 86400.0)""",
            (refresh_id, TYPE, SENDING_STALE_SECONDS),
        ).fetchone()
        if stale is None:
            conn.commit()
            return False
    conn.execute(
        """UPDATE refresh_notifications
           SET status='sending', attempts=attempts+1,
               last_attempt_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), last_error=NULL
           WHERE refresh_id=? AND notification_type=?""",
        (refresh_id, TYPE),
    )
    conn.commit()
    return True


def _pending_rows(db: Path) -> list[sqlite3.Row]:
    """Return unsent Telegram batches plus only the latest recent scheduled batch."""
    with _connect(db) as conn:
        return conn.execute(
            """SELECT r.refresh_id,r.trigger_source,r.status,r.target_count,r.events_checked,
                      r.events_with_new_evidence,r.new_evidence_count,
                      r.recommendations_count,r.no_change_count,r.error_count
               FROM refresh_runs r
               LEFT JOIN refresh_notifications n
                 ON n.refresh_id=r.refresh_id AND n.notification_type=?
               WHERE r.phase='done'
                 AND r.status IN ('completed','partial','failed')
                 AND (
                     r.trigger_source='telegram'
                     OR (
                         r.trigger_source='scheduled'
                         AND r.refresh_id=(
                             SELECT MAX(s.refresh_id)
                             FROM refresh_runs s
                             WHERE s.trigger_source='scheduled'
                         )
                         AND r.finished_at IS NOT NULL
                         AND julianday(r.finished_at) >= julianday('now') - (? / 24.0)
                     )
                 )
                 AND (
                     n.notification_id IS NULL
                     OR n.status IN ('pending','failed')
                     OR (
                         n.status='sending'
                         AND n.last_attempt_at IS NOT NULL
                         AND julianday(n.last_attempt_at) <= julianday('now') - (? / 86400.0)
                     )
                 )
               ORDER BY r.refresh_id""",
            (TYPE, SCHEDULED_MAX_AGE_HOURS, SENDING_STALE_SECONDS),
        ).fetchall()


def notify_completed_telegram_refreshes(db: Path = DEFAULT_DB) -> dict[str, int]:
    """Send once-only completion notices for interactive and scheduled refreshes.

    The historical function name is kept because collector_v10 imports it directly.
    Telegram-triggered batches retain the compact completion message. The latest
    scheduled batch uses the existing morning status renderer once it is terminal.
    """
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    rows = _pending_rows(db)

    target = os.getenv("PROROK_TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    thread = os.getenv("PROROK_TELEGRAM_THREAD_ID", DEFAULT_THREAD_ID)
    openclaw = os.getenv("OPENCLAW_BIN", "openclaw")

    for row in rows:
        with _connect(db) as conn:
            if not _claim(conn, int(row["refresh_id"])):
                counts["skipped"] += 1
                continue

        try:
            message = (
                _scheduled_message()
                if row["trigger_source"] == "scheduled"
                else _telegram_message(row)
            )
            cmd = [
                openclaw,
                "message",
                "send",
                "--channel",
                "telegram",
                "--target",
                target,
                "--message",
                message,
            ]
            if thread:
                cmd.extend(["--thread-id", thread])
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            detail = str(getattr(exc, "stderr", "") or exc)[-1000:]
            with _connect(db) as conn:
                conn.execute(
                    """UPDATE refresh_notifications SET status='failed', last_error=?
                       WHERE refresh_id=? AND notification_type=?""",
                    (detail, row["refresh_id"], TYPE),
                )
                conn.commit()
            counts["failed"] += 1
        else:
            with _connect(db) as conn:
                conn.execute(
                    """UPDATE refresh_notifications
                       SET status='sent', sent_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), last_error=NULL
                       WHERE refresh_id=? AND notification_type=?""",
                    (row["refresh_id"], TYPE),
                )
                conn.commit()
            counts["sent"] += 1
    return counts
