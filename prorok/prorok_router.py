#!/usr/bin/env python3
"""Deterministic PROROK command router for OpenClaw skills.

This script accepts the user-facing PROROK command form, for example:

    /prorok list
    /prorok show ru_capture_ukraine_oblast_center_2026
    /prorok trend ru_capture_ukraine_oblast_center_2026
    /prorok evidence ru_capture_ukraine_oblast_center_2026
    /prorok sources
    /prorok health

It then dispatches to the correct PROROK CLI helper. The goal is to keep
OpenClaw skill execution deterministic and avoid the model choosing the wrong
helper script for subcommands such as trend/evidence/sources.
"""

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
    """Resolve the deployed PROROK code directory."""
    explicit = os.getenv("PROROK_CODE_DIR")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise SystemExit(f"PROROK_CODE_DIR does not exist: {path}")

    candidates = [
        Path(__file__).resolve().parent,
        Path("/app/prorok"),
        Path("/tmp/prorok-agent-system/prorok"),
    ]
    for path in candidates:
        if (path / "prorok_cli.py").exists():
            return path

    raise SystemExit(
        "PROROK code is not available. Expected /app/prorok or "
        "/tmp/prorok-agent-system/prorok."
    )


def normalize_command(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []

    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise SystemExit(f"Could not parse PROROK command: {exc}") from exc

    if parts and parts[0].lower() == "/prorok":
        parts = parts[1:]
    elif parts and parts[0].lower() == "prorok":
        parts = parts[1:]

    return parts


def run_python(script: Path, args: Sequence[str], home: str) -> int:
    cmd = [sys.executable, str(script), "--home", home, *args]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
  /prorok evidence <event_id>
  /prorok sources
  /prorok health
""".rstrip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route /prorok commands to deterministic CLI helpers")
    parser.add_argument("command", nargs="*", help="Raw /prorok command or split command tokens")
    parser.add_argument("--home", default=os.getenv("PROROK_HOME", DEFAULT_HOME), help="PROROK home directory")
    args = parser.parse_args(argv)

    # Accept either one quoted raw command or multiple split tokens.
    raw = " ".join(args.command).strip()
    parts = normalize_command(raw)
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
        if not rest:
            print("Missing event_id. Usage: /prorok show <event_id>", file=sys.stderr)
            return 2
        return run_python(code_dir / "prorok_event_cli.py", ["show-event", rest[0]], args.home)

    if subcommand == "trend":
        if not rest:
            print("Missing event_id. Usage: /prorok trend <event_id>", file=sys.stderr)
            return 2
        return run_python(code_dir / "prorok_assessment_cli.py", ["trend", rest[0]], args.home)

    if subcommand == "evidence":
        if not rest:
            print("Missing event_id. Usage: /prorok evidence <event_id>", file=sys.stderr)
            return 2
        return run_python(
            code_dir / "prorok_evidence_cli.py",
            ["evidence", rest[0], "--show-canonical"],
            args.home,
        )

    if subcommand in {"sources", "source", "source-registry", "registry"}:
        return run_python(code_dir / "prorok_evidence_cli.py", ["sources", "--show-canonical"], args.home)

    print(f"Unknown PROROK command: {subcommand}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
