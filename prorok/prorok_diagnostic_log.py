#!/usr/bin/env python3
"""Persistent diagnostic logging helpers for PROROK.

Logs are JSONL under /data/workspace/prorok/logs by default. Records are
best-effort diagnostics only: logging failures must never break PROROK work.
Files older than the retention window are removed opportunistically.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_DIR = Path(os.environ.get("PROROK_LOG_DIR", "/data/workspace/prorok/logs"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("PROROK_LOG_RETENTION_DAYS", "30"))

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)([?&](?:api[_-]?key|token|secret|password)=)([^&#\s]+)"),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def redact_text(value: Any) -> str:
    text = str(value if value is not None else "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def cleanup_old_logs(log_dir: Path = DEFAULT_LOG_DIR, retention_days: int = DEFAULT_RETENTION_DAYS, *, now: datetime | None = None) -> int:
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    if not log_dir.exists():
        return 0
    current = now or utc_now()
    cutoff = current - timedelta(days=retention_days)
    removed = 0
    for path in log_dir.glob("*.jsonl"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def append_log(kind: str, record: dict[str, Any], *, log_dir: Path = DEFAULT_LOG_DIR, retention_days: int = DEFAULT_RETENTION_DAYS) -> Path:
    safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(kind)).strip(".-") or "diagnostic"
    log_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs(log_dir, retention_days)
    now = utc_now()
    path = log_dir / f"{safe_kind}-{now:%Y-%m-%d}.jsonl"
    payload = {
        "timestamp_utc": now.isoformat().replace("+00:00", "Z"),
        **_sanitize(record),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def list_logs(*, log_dir: Path = DEFAULT_LOG_DIR, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[Path]:
    cleanup_old_logs(log_dir, retention_days)
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
