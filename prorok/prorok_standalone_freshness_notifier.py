#!/usr/bin/env python3
"""Telegram warnings for failed standalone PROROK freshness checks."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from prorok_standalone_freshness_registry import (
    DEFAULT_REGISTRY, record_delivery, registered_jobs,
)


def notification_text(job: dict) -> str:
    result = job["result"]
    heading = ("❌ PROROK · Freshness protocol FAIL" if result["status"] == "FAIL"
               else "⚠️ PROROK · Freshness check ERROR")
    return "\n".join([
        heading,
        f"Подія: {job['event_id']}",
        f"Cron ID: {job['cron_id']}",
        f"Причина: {result['reason']}",
        "Результат OpenClaw cron сам по собі не підтверджує freshness protocol.",
    ])


def notify_once(registry: Path = DEFAULT_REGISTRY) -> dict[str, int]:
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    for job in registered_jobs(registry).values():
        result = job.get("result")
        if not result or result["status"] == "PASS" or job.get("delivery", {}).get("status") == "sent":
            counts["skipped"] += 1
            continue
        cmd = [os.getenv("OPENCLAW_BIN", "openclaw"), "message", "send",
               "--channel", "telegram", "--target", job["chat_id"],
               "--message", notification_text(job)]
        if job["thread_id"]:
            cmd.extend(["--thread-id", job["thread_id"]])
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            record_delivery(registry, cron_id=job["cron_id"], status="failed")
            counts["failed"] += 1
        else:
            record_delivery(registry, cron_id=job["cron_id"], status="sent")
            counts["sent"] += 1
    return counts
