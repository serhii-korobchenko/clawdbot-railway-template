#!/usr/bin/env python3
"""Quiet wrapper for PROROK refresh dry-run launcher.

The underlying launcher calls `openclaw cron add`, whose stdout can include the full
cron payload and therefore the full refresh prompt. This wrapper monkey-patches only
that subprocess call so the Telegram command returns a compact confirmation instead
of a very large JSON object.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import prorok_refresh_dry_run_cron as launcher


SUMMARY: dict[str, str] = {}
REAL_RUN = subprocess.run


def _extract_summary(stdout: str) -> dict[str, str]:
    text = (stdout or "").strip()
    if not text:
        return {}

    decoder = json.JSONDecoder()
    data: Any = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
            break
        except json.JSONDecodeError:
            continue

    if not isinstance(data, dict):
        return {}

    schedule = data.get("schedule")
    run_at = ""
    if isinstance(schedule, dict):
        run_at = str(schedule.get("at") or schedule.get("cron") or "")

    return {
        "cron_id": str(data.get("id") or ""),
        "run_at": run_at,
    }


def quiet_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
    if isinstance(cmd, list) and cmd[:3] == ["openclaw", "cron", "add"]:
        proc = REAL_RUN(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        SUMMARY.update(_extract_summary(proc.stdout or ""))
        if kwargs.get("check") and proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            if details:
                print(details[-2000:], file=sys.stderr)
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
        return proc
    return REAL_RUN(cmd, *args, **kwargs)


def main(argv: list[str]) -> int:
    launcher.subprocess.run = quiet_run
    result = launcher.main(argv)
    if result == 0:
        if SUMMARY.get("cron_id"):
            print(f"cron_id: {SUMMARY['cron_id']}")
        if SUMMARY.get("run_at"):
            print(f"run_at: {SUMMARY['run_at']}")
    return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
