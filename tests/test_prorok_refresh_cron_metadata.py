from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prorok"))
import prorok_refresh_dry_run_cron as launcher
import prorok_refresh_dry_run_quiet as quiet


def args():
    return SimpleNamespace(event_id="event-test", at="2m", agent="main",
        timeout_seconds=300, tools="tavily_search", no_deliver=True,
        to="chat", thread_id="112")


def test_launcher_emits_validated_cron_metadata(capsys):
    payload = {"id": "cron-123", "state": {"nextRunAtMs": 123456}}
    with patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess(
        [], 0, stdout=json.dumps(payload), stderr=""
    )) as run:
        launcher.schedule_cron(args(), "prompt")
    out = capsys.readouterr().out
    assert out.count("cron_id: cron-123") == 1
    assert "run_at: 123456" in out
    assert "--json" in run.call_args.args[0]
    assert "prompt" not in out


@pytest.mark.parametrize("payload", ["not json", "{}", '{"id": ""}'])
def test_launcher_rejects_invalid_cron_response(payload):
    with patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess(
        [], 0, stdout=payload, stderr=""
    )):
        with pytest.raises(RuntimeError):
            launcher.schedule_cron(args(), "prompt")


def test_quiet_wrapper_does_not_duplicate_metadata(capsys):
    with patch.object(quiet.launcher, "main", side_effect=lambda argv: (print("cron_id: cron-123"), 0)[1]):
        assert quiet.main([]) == 0
    assert capsys.readouterr().out.count("cron_id: cron-123") == 1


def test_quiet_wrapper_preserves_subprocess_and_restores_prompt():
    original_run = launcher.subprocess.run
    original_prompt = launcher.build_prompt
    with patch.object(quiet.launcher, "main", return_value=0):
        assert quiet.main([]) == 0
        assert launcher.subprocess.run is original_run
        assert launcher.build_prompt is original_prompt


def test_quiet_wrapper_restores_prompt_after_failure():
    original_prompt = launcher.build_prompt
    with patch.object(quiet.launcher, "main", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError, match="failed"):
            quiet.main([])
    assert launcher.build_prompt is original_prompt
