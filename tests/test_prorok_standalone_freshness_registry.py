from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prorok"))
from prorok_standalone_freshness_registry import register_job, registered_jobs


def test_registry_survives_reopen(tmp_path):
    path = tmp_path / "state" / "jobs.jsonl"
    record = register_job(path, cron_id="cron-a", event_id="event-a",
                          chat_id="-123", thread_id="112", created_at_ms=100,
                          expected_run_at_ms=200)
    assert registered_jobs(path) == {"cron-a": record}
    assert path.stat().st_mode & 0o077 == 0


def test_registry_multiple_jobs(tmp_path):
    path = tmp_path / "jobs.jsonl"
    for index in (1, 2):
        register_job(path, cron_id=f"cron-{index}", event_id="event",
                     chat_id="-123", thread_id="", created_at_ms=index)
    assert set(registered_jobs(path)) == {"cron-1", "cron-2"}


def test_registry_rejects_incomplete_record(tmp_path):
    path = tmp_path / "jobs.jsonl"
    with pytest.raises(ValueError):
        register_job(path, cron_id="", event_id="event", chat_id="-123",
                     thread_id="", created_at_ms=1)
    assert not path.exists()


def test_registry_rejects_corrupt_jsonl(tmp_path):
    path = tmp_path / "jobs.jsonl"
    path.write_text('{"kind": "registered", "cron_id": "a"}\ninvalid\n')
    with pytest.raises(ValueError, match="line 2"):
        registered_jobs(path)
