#!/usr/bin/env python3
"""PROROK automatic refresh cadence controller. Only the designated cron is mutable."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

CRON_ID = "0f4f1307-1054-47ee-ad4c-b36c73cf2bcd"
CRON_NAME = "PROROK Daily Refresh Scheduler"
SCHEDULE = "20 8 * * *"
TIMEZONE = "Europe/Kyiv"
DEFAULT_STATE = Path("/data/workspace/prorok/auto_refresh_schedule.json")
MODES = {"daily": 1, "3d": 3, "7d": 7, "off": None}

def now_kyiv():
    return dt.datetime.now(ZoneInfo(TIMEZONE))

def load_state(path):
    if not path.exists():
        return {"mode": "daily", "anchor_date": None}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("mode") not in MODES:
        raise RuntimeError("Invalid PROROK schedule state")
    if data.get("anchor_date"):
        dt.date.fromisoformat(data["anchor_date"])
    return data

def run_cron(*args):
    proc = subprocess.run(["openclaw", "cron", *args], text=True, capture_output=True, timeout=25)
    if proc.returncode != 0:
        raise RuntimeError(f"openclaw cron failed: {proc.stderr[:500]}")
    return proc.stdout

def read_job():
    jobs = json.loads(run_cron("list", "--json"))["jobs"]
    matches = [j for j in jobs if j.get("id") == CRON_ID]
    if len(matches) != 1 or matches[0].get("name") != CRON_NAME:
        raise RuntimeError("Designated PROROK cron job identity mismatch")
    job = matches[0]
    if job.get("schedule", {}).get("tz") != TIMEZONE:
        raise RuntimeError("Unexpected PROROK cron timezone")
    return job

def due_on(state, day):
    period = MODES[state["mode"]]
    if period is None:
        return False
    if period == 1 or not state.get("anchor_date"):
        return True
    return (day - dt.date.fromisoformat(state["anchor_date"])).days % period == 0 and day >= dt.date.fromisoformat(state["anchor_date"])

def next_due(state, current=None):
    current = current or now_kyiv()
    if state["mode"] == "off":
        return None
    date = current.date()
    if current.time() >= dt.time(8, 20):
        date += dt.timedelta(days=1)
    for i in range(8):
        candidate = date + dt.timedelta(days=i)
        if due_on(state, candidate):
            return dt.datetime.combine(candidate, dt.time(8, 20), ZoneInfo(TIMEZONE)).isoformat()
    return None

def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(path.parent), delete=False) as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
        temp_path = f.name
    os.replace(temp_path, path)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    sub.add_parser("due")
    setter = sub.add_parser("set")
    setter.add_argument("mode", choices=MODES)
    args = parser.parse_args(argv)
    path = args.state_file
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state(path)
        if args.action == "set":
            job = read_job()
            expected_active = args.mode != "off"
            if state["mode"] != args.mode or bool(job.get("enabled")) != expected_active:
                if args.mode == "off":
                    run_cron("edit", CRON_ID, "--disable")
                else:
                    run_cron("edit", CRON_ID, "--cron", SCHEDULE, "--tz", TIMEZONE, "--exact", "--enable")
                state = {"mode": args.mode, "anchor_date": (now_kyiv().date() + dt.timedelta(days=1)).isoformat()}
                save_state(path, state)
            job = read_job()
            if bool(job.get("enabled")) != expected_active:
                raise RuntimeError("Cron enabled state failed verification")
        if args.action == "due":
            result = {"due": due_on(state, now_kyiv().date()), "mode": state["mode"]}
        else:
            job = read_job()
            result = {"mode": state["mode"], "enabled": job.get("enabled"), "cron_id": CRON_ID, "next_due": next_due(state), "anchor_date": state.get("anchor_date")}
        print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"PROROK schedule error: {exc}", file=sys.stderr)
        raise SystemExit(1)
