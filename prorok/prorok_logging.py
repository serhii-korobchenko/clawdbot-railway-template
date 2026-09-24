#!/usr/bin/env python3
"""Persistent structured operational logging for PROROK.

Logs are JSONL files under /data/workspace/prorok/logs by default.  Records are
small, append-only, secret-redacted, and retained for 30 days.  This module is
stdlib-only so refresh launchers and deterministic CLIs can use it safely.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LOG_DIR = Path(os.getenv("PROROK_LOG_DIR", "/data/workspace/prorok/logs"))
DEFAULT_RETENTION_DAYS = 30

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|cookie|credential)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]+=*")
_URL_SECRET = re.compile(
    r"(?i)([?&](?:token|key|api_key|apikey|access_token|auth)=)[^&#\s]+"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:token|secret|password|passwd|authorization|api[_-]?key|"
    r"access[_-]?token|cookie|credential)\b\s*[:=]\s*)([^\s,;]+)"
)
_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|tvly-[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"\d{8,12}:[A-Za-z0-9_-]{20,})\b"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    value = value or utc_now()
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _redact_string(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _URL_SECRET.sub(r"\1[REDACTED]", value)
    value = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return _KNOWN_TOKEN.sub("[REDACTED]", value)


def redact(value: Any, key: str = "") -> Any:
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def log_path(log_dir: Path, when: datetime | None = None) -> Path:
    when = when or utc_now()
    return log_dir / f"prorok-{when:%Y-%m-%d}.jsonl"


def cleanup_old_logs(
    log_dir: Path = DEFAULT_LOG_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> int:
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    if not log_dir.exists():
        return 0
    now = now or utc_now()
    cutoff = (now - timedelta(days=retention_days)).date()
    removed = 0
    for path in log_dir.glob("prorok-*.jsonl"):
        try:
            stamp = datetime.strptime(path.stem.removeprefix("prorok-"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def write_log(
    event: str,
    *,
    component: str,
    level: str = "info",
    log_dir: Path = DEFAULT_LOG_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    **fields: Any,
) -> Path:
    now = utc_now()
    log_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs(log_dir, retention_days, now)
    record = {
        "timestamp_utc": utc_iso(now),
        "level": str(level),
        "component": str(component),
        "event": str(event),
        **fields,
    }
    record = redact(record)
    path = log_path(log_dir, now)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path
