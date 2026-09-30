import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "prorok" / "prorok_app_tracking_cli.py"
MIGRATION = ROOT / "prorok" / "migrations" / "017_prorok_app_tracking.py"


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        INSERT INTO meta(key,value) VALUES('schema_version','16');
        CREATE TABLE evidence_items(evidence_id INTEGER PRIMARY KEY, summary TEXT);
        INSERT INTO evidence_items(evidence_id,summary) VALUES(19,'test evidence');
        """
    )
    conn.commit()
    conn.close()
    subprocess.run([sys.executable, str(MIGRATION), "--db", str(path)], check=True, capture_output=True, text=True)


def run_cli(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), "--db", str(path), *args], capture_output=True, text=True)


def test_mark_unmark_is_append_only_and_idempotent(tmp_path):
    db = tmp_path / "prorok.sqlite3"
    make_db(db)

    initial = run_cli(db, "show", "19")
    assert initial.returncode == 0
    assert "state: unmarked" in initial.stdout

    marked = run_cli(db, "set", "19", "--state", "marked", "--source", "telegram", "--actor", "telegram:123")
    assert marked.returncode == 0
    assert "state: marked" in marked.stdout
    assert "changed: true" in marked.stdout

    replay = run_cli(db, "set", "19", "--state", "marked", "--source", "telegram", "--actor", "telegram:123")
    assert replay.returncode == 0
    assert "state: marked" in replay.stdout
    assert "changed: false" in replay.stdout

    unmarked = run_cli(db, "set", "19", "--state", "unmarked", "--source", "telegram", "--actor", "telegram:123")
    assert unmarked.returncode == 0
    assert "state: unmarked" in unmarked.stdout
    assert "changed: true" in unmarked.stdout

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT state,source,actor FROM evidence_prorok_app_status_history WHERE evidence_id=19 ORDER BY prorok_app_status_history_id"
    ).fetchall()
    conn.close()
    assert rows == [
        ("marked", "telegram", "telegram:123"),
        ("unmarked", "telegram", "telegram:123"),
    ]


def test_missing_evidence_is_rejected(tmp_path):
    db = tmp_path / "prorok.sqlite3"
    make_db(db)
    result = run_cli(db, "set", "999", "--state", "marked", "--source", "telegram")
    assert result.returncode == 1
    assert "evidence not found: 999" in result.stderr
