#!/usr/bin/env python3
"""Deterministic PROROK command router for OpenClaw skills."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

DEFAULT_HOME = "/data/workspace/prorok"


def resolve_code_dir() -> Path:
    explicit = os.getenv("PROROK_CODE_DIR")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise SystemExit(f"PROROK_CODE_DIR does not exist: {path}")

    candidates = [Path(__file__).resolve().parent, Path("/app/prorok"), Path("/tmp/prorok-agent-system/prorok")]
    for path in candidates:
        if (path / "prorok_cli.py").exists():
            return path
    raise SystemExit("PROROK code is not available. Expected /app/prorok or /tmp/prorok-agent-system/prorok.")


def normalize_command(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise SystemExit(f"Could not parse PROROK command: {exc}") from exc
    if parts and parts[0].lower() in {"/prorok", "prorok"}:
        parts = parts[1:]
    return parts


def run_python(script: Path, args: Sequence[str], home: str) -> int:
    proc = subprocess.run([sys.executable, str(script), "--home", home, *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def run_plain_python(script: Path, args: Sequence[str]) -> int:
    proc = subprocess.run([sys.executable, str(script), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def usage() -> str:
    return """PROROK commands:
  /prorok list
  /prorok show <event_id>
  /prorok trend <event_id>
  /prorok latest <event_id>
  /prorok evidence <event_id>
  /prorok sources
  /prorok health
  /prorok refresh <event_id> [--to <chat_id>] [--thread-id <topic_id>] [--at 2m] [--no-schedule]
  /prorok refresh-all [--status active] [--limit 50] [--start-at 2m] [--spacing-minutes 3] [--to <chat_id>] [--thread-id <topic_id>] [--no-schedule]
  /prorok add-event ...
  /prorok assess ...
  /prorok add-evidence ...""".rstrip()


def require_args(rest: Sequence[str], message: str) -> bool:
    if rest:
        return True
    print(message, file=sys.stderr)
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route /prorok commands to deterministic CLI helpers")
    parser.add_argument("command", nargs="*", help="Raw /prorok command or split command tokens")
    parser.add_argument("--home", default=os.getenv("PROROK_HOME", DEFAULT_HOME), help="PROROK home directory")
    args = parser.parse_args(argv)

    parts = normalize_command(" ".join(args.command).strip())
    if not parts:
        print(usage())
        return 2

    subcommand = parts[0].lower()
    rest = parts[1:]
    code_dir = resolve_code_dir()

    if subcommand in {"help", "--help", "-h"}:
        print(usage())
        return 0
    if subcommand in {"health", "status"}:
        return run_python(code_dir / "prorok_cli.py", ["health"], args.home)
    if subcommand in {"list", "events"}:
        return run_python(code_dir / "prorok_event_cli.py", ["list-events", "--status", "all"], args.home)
    if subcommand == "show":
        if not require_args(rest, "Missing event_id. Usage: /prorok show <event_id>"):
            return 2
        return run_python(code_dir / "prorok_event_cli.py", ["show-event", rest[0]], args.home)
    if subcommand == "trend":
        if not require_args(rest, "Missing event_id. Usage: /prorok trend <event_id>"):
            return 2
        return run_python(code_dir / "prorok_assessment_cli.py", ["trend", rest[0]], args.home)
    if subcommand == "latest":
        if not require_args(rest, "Missing event_id. Usage: /prorok latest <event_id>"):
            return 2
        return run_python(code_dir / "prorok_assessment_cli.py", ["latest", rest[0]], args.home)
    if subcommand == "evidence":
        if not require_args(rest, "Missing event_id. Usage: /prorok evidence <event_id>"):
            return 2
        return run_python(code_dir / "prorok_evidence_cli.py", ["evidence", rest[0], "--show-canonical"], args.home)
    if subcommand in {"sources", "source", "source-registry", "registry"}:
        return run_python(code_dir / "prorok_evidence_cli.py", ["sources", "--show-canonical"], args.home)
    if subcommand in {"refresh-all", "refresh-all-dry-run", "dry-run-all"}:
        script = code_dir / "prorok_refresh_all_dry_run_quiet.py"
        if not script.exists():
            print(f"PROROK refresh-all launcher is not available: {script}", file=sys.stderr)
            return 2
        return run_plain_python(script, rest)
    if subcommand in {"refresh", "dry-run", "refresh-dry-run"}:
        if not require_args(rest, "Missing event_id. Usage: /prorok refresh <event_id>"):
            return 2
        quiet = code_dir / "prorok_refresh_dry_run_quiet.py"
        script = quiet if quiet.exists() else code_dir / "prorok_refresh_dry_run_cron.py"
        if not script.exists():
            print(f"PROROK refresh launcher is not available: {script}", file=sys.stderr)
            return 2
        return run_plain_python(script, rest)
    if subcommand in {"add-event", "create-event", "event-add"}:
        if not rest:
            print("Missing add-event arguments.", file=sys.stderr)
            return 2
        return run_python(code_dir / "prorok_event_cli.py", ["add-event", *rest], args.home)
    if subcommand in {"assess", "add-assessment", "assessment", "assessment-add"}:
        if not rest:
            print("Missing assessment arguments.", file=sys.stderr)
            return 2
        return run_python(code_dir / "prorok_assessment_cli.py", ["add-assessment", *rest], args.home)
    if subcommand in {"add-evidence", "evidence-add", "add-source", "source-add"}:
        if not rest:
            print("Missing evidence arguments.", file=sys.stderr)
            return 2
        return run_python(code_dir / "prorok_evidence_cli.py", ["add-evidence", *rest], args.home)

    print(f"Unknown PROROK command: {subcommand}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
