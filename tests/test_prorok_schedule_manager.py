from __future__ import annotations
import datetime as dt
import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "prorok" / "prorok_schedule_cli.py"
spec = importlib.util.spec_from_file_location("prorok_schedule_cli", MODULE)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)

def test_calendar_intervals_cross_month():
    state = {"mode": "3d", "anchor_date": "2026-10-30"}
    assert schedule.due_on(state, dt.date(2026, 11, 2))
    assert not schedule.due_on(state, dt.date(2026, 11, 1))
    state["mode"] = "7d"
    assert schedule.due_on(state, dt.date(2026, 11, 6))

def test_off_and_daily_modes():
    assert not schedule.due_on({"mode": "off", "anchor_date": None}, dt.date(2026, 10, 26))
    assert schedule.due_on({"mode": "daily", "anchor_date": None}, dt.date(2026, 10, 26))

def test_next_due_keeps_kyiv_local_time():
    now = dt.datetime(2026, 10, 26, 9, 0, tzinfo=schedule.ZoneInfo("Europe/Kyiv"))
    got = schedule.next_due({"mode": "daily", "anchor_date": None}, now)
    assert got.startswith("2026-10-27T08:20:00+")

def test_state_persistence(tmp_path):
    path = tmp_path / "state.json"
    state = {"mode": "7d", "anchor_date": "2026-10-27"}
    schedule.save_state(path, state)
    assert schedule.load_state(path) == state

def test_telegram_menu_and_guard():
    root = MODULE.parents[1]
    ui = (root / "prorok_telegram/src/index.js").read_text(encoding="utf-8")
    assert '"🕒 Автоматичне оновлення"' in ui
    assert 'schedule-confirm:' in ui and 'schedule-apply:' in ui
    wrapper = (root / "prorok/prorok_scheduled_refresh.sh").read_text(encoding="utf-8")
    assert "prorok_schedule_cli.py due" in wrapper

def test_router_supports_schedule_fallback():
    router = (MODULE.parents[1] / "prorok/prorok_router.py").read_text(encoding="utf-8")
    assert 'if subcommand == "schedule":' in router
    assert '["set", rest[0]] if rest else ["status"]' in router

def test_due_guard_checks_real_cron():
    source = MODULE.read_text(encoding="utf-8")
    assert 'Cron and persisted cadence are inconsistent' in source
    assert 'job.get("schedule", {}).get("expr") != SCHEDULE' in source

def test_telegram_text_schedule_routing():
    ui = (MODULE.parents[1] / "prorok_telegram/src/index.js").read_text(encoding="utf-8")
    assert "acceptsArgs: true" in ui
    assert 'const raw = String(ctx?.args || "").trim();' in ui
    assert 'scheduleConfirmPresentation(parts[1])' in ui
    assert '"/app/prorok/prorok_router.py", raw' in ui
