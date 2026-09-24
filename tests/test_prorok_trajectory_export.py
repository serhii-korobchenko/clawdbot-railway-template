import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from prorok.prorok_trajectory_export import export_trajectory, load_latest_result, load_result, safe_name


def make_db(path: Path, session_key: str | None = "agent:prorok-refresh:cron:abc") -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY,
            refresh_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            cron_id TEXT,
            session_id TEXT,
            session_key TEXT,
            job_state TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO refresh_event_results VALUES(7, 3, 'event-a', 'abc', 'sid', ?, 'completed')",
        (session_key,),
    )
    conn.commit()
    conn.close()


def test_load_result_and_safe_name(tmp_path):
    db = tmp_path / "p.sqlite3"
    make_db(db)
    row = load_result(db, 7)
    assert row["session_key"] == "agent:prorok-refresh:cron:abc"
    assert safe_name(row) == "refresh-3-result-7"


def test_load_latest_result(tmp_path):
    db = tmp_path / "p.sqlite3"
    make_db(db)
    row = load_latest_result(db)
    assert row["refresh_event_result_id"] == 7


def test_export_trajectory_uses_collected_session_key(tmp_path, monkeypatch):
    db = tmp_path / "p.sqlite3"
    make_db(db)
    row = load_result(db, 7)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        output_name = cmd[cmd.index("--output") + 1]
        bundle = tmp_path / ".openclaw" / "trajectory-exports" / output_name
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"outputDir": str(bundle)}), stderr="")

    monkeypatch.setattr("prorok.prorok_trajectory_export.subprocess.run", fake_run)
    result = export_trajectory(
        row,
        workspace=tmp_path,
        export_root=tmp_path / "exports",
        openclaw_bin="openclaw",
    )

    assert seen["cmd"][:3] == ["openclaw", "sessions", "export-trajectory"]
    assert seen["cmd"][seen["cmd"].index("--session-key") + 1] == "agent:prorok-refresh:cron:abc"
    assert seen["cmd"][seen["cmd"].index("--output") + 1] == "refresh-3-result-7"
    assert Path(result["archive_path"]).is_file()
    assert result["archive_path"].endswith(".zip")


def test_export_requires_collected_session_key(tmp_path):
    db = tmp_path / "p.sqlite3"
    make_db(db, None)
    row = load_result(db, 7)
    with pytest.raises(SystemExit, match="session_key"):
        export_trajectory(
            row,
            workspace=tmp_path,
            export_root=tmp_path / "exports",
            openclaw_bin="openclaw",
        )
