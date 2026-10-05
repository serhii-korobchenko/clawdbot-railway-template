from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prorok"))
import prorok_standalone_freshness_watcher as watcher
from prorok_standalone_freshness_registry import register_job, registered_jobs


def setup(tmp_path):
    registry = tmp_path / "jobs.jsonl"
    state = tmp_path / "state"
    register_job(registry, cron_id="cron-a", event_id="event-a",
                 chat_id="chat", thread_id="112", created_at_ms=100,
                 expected_run_at_ms=200)
    return registry, state


def finish(state, status="ok", session="session-a"):
    path = state / "cron" / "runs" / "cron-a.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"jobId": "cron-a", "action": "finished",
                                "runAtMs": 200, "status": status,
                                "sessionId": session}) + "\n")


def test_pass_persists_and_restart_skips(tmp_path):
    registry, state = setup(tmp_path)
    finish(state)
    with patch.object(watcher.collector, "resolve_session_transcript_path", return_value=Path("session")), patch.object(
        watcher.freshness, "check_freshness", return_value=(True, "verified", 2)
    ) as check:
        assert watcher.collect_once(registry, state, now_ms=300)["PASS"] == 1
        assert watcher.collect_once(registry, state, now_ms=400)["PASS"] == 0
        check.assert_called_once()
    assert registered_jobs(registry)["cron-a"]["result"]["status"] == "PASS"


def test_fail_persists(tmp_path):
    registry, state = setup(tmp_path)
    finish(state)
    with patch.object(watcher.collector, "resolve_session_transcript_path", return_value=Path("session")), patch.object(
        watcher.freshness, "check_freshness", return_value=(False, "missing verification", 1)
    ):
        assert watcher.collect_once(registry, state, now_ms=300)["FAIL"] == 1
    assert registered_jobs(registry)["cron-a"]["result"]["reason"] == "missing verification"


def test_missing_transcript_waits_then_errors(tmp_path):
    registry, state = setup(tmp_path)
    finish(state)
    assert watcher.collect_once(registry, state, now_ms=300, transcript_grace_ms=1000)["pending"] == 1
    assert watcher.collect_once(registry, state, now_ms=1201, transcript_grace_ms=1000)["ERROR"] == 1


def test_no_finished_run_times_out(tmp_path):
    registry, state = setup(tmp_path)
    assert watcher.collect_once(registry, state, now_ms=300, transcript_grace_ms=1000)["pending"] == 1
    assert watcher.collect_once(registry, state, now_ms=1201, transcript_grace_ms=1000)["ERROR"] == 1


def test_failed_cron_is_error(tmp_path):
    registry, state = setup(tmp_path)
    finish(state, status="error")
    assert watcher.collect_once(registry, state, now_ms=300)["ERROR"] == 1
