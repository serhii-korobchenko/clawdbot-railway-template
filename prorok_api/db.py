from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import DatabaseUnavailable


REQUIRED_TABLES = frozenset({
    "events",
    "assessments",
    "evidence_items",
    "sources",
    "runs",
})


@contextmanager
def readonly_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    """Open one consistent read-only snapshot and close it after use."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=5.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN")
    except sqlite3.Error as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        raise DatabaseUnavailable(str(exc)) from exc

    try:
        yield conn
    finally:
        try:
            if conn.in_transaction:
                conn.rollback()
        finally:
            conn.close()


def validate_database(db_path: str) -> None:
    path = Path(db_path)
    if not path.is_file():
        raise DatabaseUnavailable("PROROK database file does not exist")

    try:
        with readonly_connection(db_path) as conn:
            conn.execute("SELECT 1").fetchone()
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            present = {row["name"] for row in rows}
            missing = REQUIRED_TABLES - present
            if missing:
                raise DatabaseUnavailable(
                    "PROROK database schema is missing required tables: "
                    + ", ".join(sorted(missing))
                )
    except sqlite3.Error as exc:
        raise DatabaseUnavailable(str(exc)) from exc
