from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prorok"))
import prorok_standalone_freshness_notifier as notifier
from prorok_standalone_freshness_registry import (
    record_result, register_job, registered_jobs,
)


def setup(tmp_path, status):
    registry = tmp_path / "jobs.jsonl"
    register_job(registry, cron_id="cron-a", event_id="event-a",
                 chat_id="-123", thread_id="112", created_at_ms=100)
    record_result(registry, cron_id="cron-a", status=status,
                  reason="missing verification", required=2)
    return registry


def test_pass_does_not_send(tmp_path):
    registry = setup(tmp_path, "PASS")
    with patch.object(notifier.subprocess, "run") as send:
        assert notifier.notify_once(registry)["sent"] == 0
        send.assert_not_called()


def test_fail_sends_once_and_survives_restart(tmp_path):
    registry = setup(tmp_path, "FAIL")
    with patch.object(notifier.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as send:
        assert notifier.notify_once(registry)["sent"] == 1
        assert notifier.notify_once(registry)["sent"] == 0
        send.assert_called_once()
        assert "--thread-id" in send.call_args.args[0]
        assert "Freshness protocol FAIL" in send.call_args.args[0][send.call_args.args[0].index("--message") + 1]
    assert registered_jobs(registry)["cron-a"]["delivery"]["status"] == "sent"


def test_error_sends_distinct_warning(tmp_path):
    registry = setup(tmp_path, "ERROR")
    with patch.object(notifier.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as send:
        assert notifier.notify_once(registry)["sent"] == 1
        assert "Freshness check ERROR" in send.call_args.args[0][send.call_args.args[0].index("--message") + 1]


def test_failed_send_retries(tmp_path):
    registry = setup(tmp_path, "FAIL")
    with patch.object(notifier.subprocess, "run", side_effect=subprocess.TimeoutExpired("openclaw", 30)):
        assert notifier.notify_once(registry)["failed"] == 1
    assert registered_jobs(registry)["cron-a"]["delivery"]["status"] == "failed"
    with patch.object(notifier.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
        assert notifier.notify_once(registry)["sent"] == 1
    assert registered_jobs(registry)["cron-a"]["delivery"]["status"] == "sent"
