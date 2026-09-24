#!/usr/bin/env python3
"""Export a redacted OpenClaw trajectory for one collected PROROK refresh result."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import shutil
import sys
from pathlib import Path

DEFAULT_DB = Path("/data/workspace/prorok/prorok.sqlite3")
DEFAULT_WORKSPACE = Path("/data/workspace")
DEFAULT_EXPORT_ROOT = Path("/data/workspace/prorok/logs/trajectory-exports")


def load_result(db: Path, result_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT refresh_event_result_id, refresh_id, event_id, cron_id,
                   session_id, session_key, job_state
            FROM refresh_event_results
            WHERE refresh_event_result_id = ?
            """,
            (result_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"refresh_event_result_id not found: {result_id}")
    return row


def load_latest_result(db: Path) -> sqlite3.Row:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT refresh_event_result_id, refresh_id, event_id, cron_id,
                   session_id, session_key, job_state
            FROM refresh_event_results
            WHERE session_key IS NOT NULL AND TRIM(session_key) <> ''
            ORDER BY refresh_event_result_id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit("trajectory unavailable: no collected result with session_key")
    return row


def load_refresh_results(db: Path, refresh_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT refresh_event_result_id, refresh_id, event_id, cron_id,
                   session_id, session_key, job_state
            FROM refresh_event_results
            WHERE refresh_id = ?
            ORDER BY refresh_event_result_id
            """,
            (refresh_id,),
        ).fetchall()
    finally:
        conn.close()
    return list(rows)


def normalize_session_key(session_key: str) -> str:
    """Map collector run-specific keys to the stored OpenClaw session key."""
    marker = ":run:"
    if marker in session_key:
        return session_key.split(marker, 1)[0]
    return session_key


def safe_name(row: sqlite3.Row) -> str:
    return f"refresh-{int(row['refresh_id'])}-result-{int(row['refresh_event_result_id'])}"


def export_trajectory(
    row: sqlite3.Row,
    *,
    workspace: Path,
    export_root: Path,
    openclaw_bin: str,
) -> dict[str, object]:
    collected_session_key = str(row["session_key"] or "").strip()
    if not collected_session_key:
        raise SystemExit(
            "trajectory unavailable: collector has not recorded session_key for this result"
        )

    session_key = normalize_session_key(collected_session_key)

    export_root.mkdir(parents=True, exist_ok=True)
    output_name = safe_name(row)
    cmd = [
        openclaw_bin,
        "sessions",
        "export-trajectory",
        "--session-key",
        session_key,
        "--workspace",
        str(workspace),
        "--output",
        output_name,
        "--json",
    ]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SystemExit(f"trajectory export failed: {detail[-2000:]}")

    payload: object
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout.strip()}

    actual_output_dir = workspace / ".openclaw" / "trajectory-exports" / output_name
    if isinstance(payload, dict) and payload.get("outputDir"):
        actual_output_dir = Path(str(payload["outputDir"]))
    if not actual_output_dir.is_dir():
        raise SystemExit(f"trajectory export directory not found: {actual_output_dir}")

    archive_base = export_root / output_name
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=actual_output_dir))

    return {
        "refresh_id": int(row["refresh_id"]),
        "refresh_event_result_id": int(row["refresh_event_result_id"]),
        "event_id": str(row["event_id"]),
        "cron_id": str(row["cron_id"] or ""),
        "session_id": str(row["session_id"] or ""),
        "session_key": session_key,
        "collected_session_key": collected_session_key,
        "job_state": str(row["job_state"] or ""),
        "output_name": output_name,
        "export_root": str(export_root),
        "archive_path": str(archive_path),
        "openclaw": payload,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("refresh_event_result_id", type=int, nargs="?")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--openclaw-bin", default="openclaw")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.latest:
        row = load_latest_result(args.db)
    elif args.refresh_event_result_id is not None:
        row = load_result(args.db, args.refresh_event_result_id)
    else:
        raise SystemExit("provide refresh_event_result_id or --latest")
    result = export_trajectory(
        row,
        workspace=args.workspace,
        export_root=args.export_root,
        openclaw_bin=args.openclaw_bin,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
