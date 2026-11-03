#!/usr/bin/env python3
"""Durable append-only registry for standalone PROROK freshness checks.

This operational JSONL is separate from the PROROK SQLite database. Only
Telegram-delivered standalone jobs are registered; tracked batch jobs are not.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_REGISTRY = Path("/data/workspace/prorok/standalone_freshness_jobs.jsonl")


def append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError("short registry write")
        os.fsync(fd)
    finally:
        os.close(fd)


def record_result(path: Path, *, cron_id: str, status: str, reason: str, required: int) -> None:
    if status not in {"PASS", "FAIL", "ERROR"} or not cron_id:
        raise ValueError("invalid freshness result")
    append_record(path, {"kind": "result", "cron_id": cron_id,
                         "status": status, "reason": reason, "required": required})


def record_delivery(path: Path, *, cron_id: str, status: str) -> None:
    if not cron_id or status not in {"sent", "failed"}:
        raise ValueError("invalid freshness delivery status")
    append_record(path, {"kind": "delivery", "cron_id": cron_id, "status": status})


def register_job(path: Path, *, cron_id: str, event_id: str,
                 chat_id: str, thread_id: str, created_at_ms: int,
                 expected_run_at_ms: int | None = None) -> dict:
    if not cron_id or not event_id or not chat_id or created_at_ms <= 0:
        raise ValueError("incomplete standalone freshness registration")
    record = {
        "kind": "registered", "cron_id": cron_id, "event_id": event_id,
        "chat_id": chat_id, "thread_id": thread_id,
        "created_at_ms": created_at_ms,
        "expected_run_at_ms": expected_run_at_ms,
    }
    append_record(path, record)
    return record


def registered_jobs(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    jobs: dict[str, dict] = {}
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid registry JSONL line {number}") from exc
            if not isinstance(item, dict) or not isinstance(item.get("cron_id"), str):
                raise ValueError(f"invalid registry entry at line {number}")
            if item.get("kind") == "registered":
                jobs[item["cron_id"]] = item
            elif item.get("kind") == "result" and item["cron_id"] in jobs:
                jobs[item["cron_id"]] = {**jobs[item["cron_id"]], "result": item}
            elif item.get("kind") == "delivery" and item["cron_id"] in jobs:
                jobs[item["cron_id"]] = {**jobs[item["cron_id"]], "delivery": item}
    return jobs
