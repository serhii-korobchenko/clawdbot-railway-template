import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from prorok.prorok_trajectory_export import export_refresh_bundle, export_trajectory, load_latest_result, load_refresh_results, load_result, normalize_session_key, safe_name


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


def test_normalize_run_specific_session_key():
    assert normalize_session_key(
        "agent:prorok-refresh:cron:abc:run:session-123"
    ) == "agent:prorok-refresh:cron:abc"
    assert normalize_session_key(
        "agent:prorok-refresh:cron:abc"
    ) == "agent:prorok-refresh:cron:abc"


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



def test_full_refresh_bundle_redacts_secrets_and_keeps_diagnostics(tmp_path, monkeypatch):
    db = tmp_path / "p.sqlite3"
    make_db(db)

    def fake_run(cmd, **kwargs):
        output_name = cmd[cmd.index("--output") + 1]
        bundle = tmp_path / ".openclaw" / "trajectory-exports" / output_name
        bundle.mkdir(parents=True)
        (bundle / "events.jsonl").write_text(
            json.dumps({
                "query": "Ukraine evidence last 24 hours",
                "url": "https://example.test/story?api_key=TOPSECRET&q=ukraine",
                "Authorization": "Bearer VERYSECRET",
                "decision": "rejected: stale",
            }) + "\n",
            encoding="utf-8",
        )
        (bundle / "metadata.json").write_text(
            json.dumps({"session_id": "sid", "token": "TOPSECRET"}),
            encoding="utf-8",
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"outputDir": str(bundle)}),
            stderr="",
        )

    monkeypatch.setattr("prorok.prorok_trajectory_export.subprocess.run", fake_run)
    result = export_refresh_bundle(
        3,
        db=db,
        workspace=tmp_path,
        export_root=tmp_path / "exports",
        openclaw_bin="openclaw",
    )

    import zipfile
    with zipfile.ZipFile(result["archive_path"]) as zf:
        names = zf.namelist()
        events_name = next(name for name in names if name.endswith("/events.jsonl"))
        text = zf.read(events_name).decode("utf-8")
        assert "Ukraine evidence last 24 hours" in text
        assert "rejected: stale" in text
        assert "TOPSECRET" not in text
        assert "VERYSECRET" not in text
        assert "api_key=[REDACTED]" in text
        metadata_name = next(name for name in names if name.endswith("/metadata.json"))
        metadata = zf.read(metadata_name).decode("utf-8")
        assert "TOPSECRET" not in metadata
        assert "[REDACTED]" in metadata
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["refresh_id"] == 3
        assert manifest["secret_redaction"] == "applied"
