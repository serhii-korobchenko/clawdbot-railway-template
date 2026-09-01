import sqlite3

import pytest

from prorok_api.db import readonly_connection


def test_connection_is_query_only_and_rejects_write(db_path):
    with readonly_connection(str(db_path)) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("SELECT CASEFOLD('ЯДЕРНА')").fetchone()[0] == "ядерна"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO events(event_id, title, question, status, created_at, updated_at) "
                "VALUES ('forbidden', 'x', 'x', 'active', 'x', 'x')"
            )

    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_id='forbidden'"
    ).fetchone()[0]
    conn.close()
    assert count == 0
