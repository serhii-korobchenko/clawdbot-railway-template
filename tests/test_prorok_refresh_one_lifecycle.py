from __future__ import annotations

import subprocess
from pathlib import Path

from prorok import prorok_refresh_one_lifecycle as one
from prorok import prorok_router as router


def test_single_refresh_creates_lifecycle_and_records_cron(monkeypatch, tmp_path: Path, capsys) -> None:
    target = one.batch.RefreshTarget(
        event_id="event_a",
        title="Event A",
        status="active",
        forecast_horizon="2026-12-31",
        updated_at="2026-09-13T00:00:00Z",
        baseline_assessment_id=10,
        baseline_probability=70,
    )
    calls: dict[str, object] = {}

    monkeypatch.setattr(one, "load_exact_target", lambda db, event_id: target)
    monkeypatch.setattr(one.batch, "resolve_refresh_script", lambda: Path("/tmp/single.py"))

    def create_batch(db, targets, trigger_source):
        calls["create"] = (db, targets, trigger_source)
        return 22, {"event_a": 55}

    monkeypatch.setattr(one.batch, "create_refresh_batch", create_batch)
    monkeypatch.setattr(
        one.batch,
        "run_one",
        lambda script, target_arg, args, at: subprocess.CompletedProcess(
            ["python"], 0, "cron_id: cron-123\nrun_at: 2026-09-13T20:00:00Z\n", ""
        ),
    )
    monkeypatch.setattr(
        one.batch,
        "mark_schedule_result",
        lambda db, refresh_id, child_id, **kwargs: calls.setdefault(
            "mark", (db, refresh_id, child_id, kwargs)
        ),
    )
    monkeypatch.setattr(one.batch, "finalize_scheduling", lambda db, refresh_id: (1, 0))

    rc = one.main([
        "event_a",
        "--db",
        str(tmp_path / "db.sqlite3"),
        "--at",
        "1m",
        "--trigger-source",
        "telegram",
    ])

    assert rc == 0
    assert calls["create"][2] == "telegram"
    assert calls["create"][1] == [target]
    assert calls["mark"][1:3] == (22, 55)
    assert calls["mark"][3]["scheduled"] is True
    assert calls["mark"][3]["cron_id"] == "cron-123"
    out = capsys.readouterr().out
    assert "refresh_id: 22" in out
    assert "refresh_event_result_id: 55" in out
    assert "result: ok" in out


def test_router_refresh_uses_single_event_lifecycle(monkeypatch, tmp_path: Path) -> None:
    for name in ("prorok_cli.py", "prorok_refresh_one_lifecycle.py"):
        (tmp_path / name).write_text("# test\n", encoding="utf-8")

    monkeypatch.setenv("PROROK_CODE_DIR", str(tmp_path))
    calls: dict[str, object] = {}

    def fake_run(script, args):
        calls["script"] = script
        calls["args"] = list(args)
        return 0

    monkeypatch.setattr(router, "run_plain_python", fake_run)

    rc = router.main(["/prorok refresh event_a --at 1m"])

    assert rc == 0
    assert calls["script"] == tmp_path / "prorok_refresh_one_lifecycle.py"
    assert calls["args"] == ["--trigger-source", "telegram", "event_a", "--at", "1m"]
