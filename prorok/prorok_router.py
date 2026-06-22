#!/usr/bin/env python3
"""Deterministic PROROK command router for OpenClaw skills.

This script accepts the user-facing PROROK command form, for example:

    /prorok list
    /prorok show ru_capture_ukraine_oblast_center_2026
    /prorok trend ru_capture_ukraine_oblast_center_2026
    /prorok evidence ru_capture_ukraine_oblast_center_2026
    /prorok sources
    /prorok health

Write commands are also supported and are passed through to the lower-level
PROROK CLI helpers:

    /prorok add-event --event-id example_2026 --title "..." --question "..."
    /prorok assess example_2026 --probability 35 --band "25-35%" --label "Малоймовірно" --rationale "..."
    /prorok add-evidence example_2026 --url "https://..." --direction indicator --summary "..."

The goal is to keep OpenClaw skill execution deterministic and avoid the model
choosing the wrong helper script for PROROK subcommands.
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
  Read/state:
    /prorok list
    /prorok show <event_id>
    /prorok trend <event_id>
    /prorok latest <event_id>
    /prorok evidence <event_id>
    /prorok sources
    /prorok health

  Write/update:
    /prorok add-event --event-id <id> --title "<title>" --question "<question>" [--forecast-horizon YYYY-MM-DD] [--criteria "..."] [--tags "a,b,c"]
    /prorok assess <event_id> --probability <0-100> --band "<band>" --label "<label>" [--confidence low|medium|high] --rationale "<text>"
    /prorok add-evidence <event_id> --url "<url>" --direction indicator|counterindicator|neutral --summary "<text>" [--title "..."] [--strength weak|medium|strong] [--relevance 0-100] [--credibility 0-100]
""".rstrip()


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
        return run_python(
            code_dir / "prorok_evidence_cli.py",
            ["evidence", rest[0], "--show-canonical"],
            args.home,
        )

    if subcommand in {"sources", "source", "source-registry", "registry"}:
        return run_python(code_dir / "prorok_evidence_cli.py", ["sources", "--show-canonical"], args.home)

    if subcommand in {"add-event", "create-event", "event-add"}:
        if not rest:
            print(
                "Missing add-event arguments. Usage: /prorok add-event --event-id <id> --title \"<title>\" --question \"<question>\"",
                file=sys.stderr,
            )
            return 2
        return run_python(code_dir / "prorok_event_cli.py", ["add-event", *rest], args.home)

    if subcommand in {"assess", "add-assessment", "assessment", "assessment-add"}:
        if not rest:
            print(
                "Missing assessment arguments. Usage: /prorok assess <event_id> --probability <0-100> --band \"<band>\" --label \"<label>\" --rationale \"<text>\"",
                file=sys.stderr,
            )
            return 2
        return run_python(code_dir / "prorok_assessment_cli.py", ["add-assessment", *rest], args.home)

    if subcommand in {"add-evidence", "evidence-add", "add-source", "source-add"}:
        if not rest:
            print(
                "Missing evidence arguments. Usage: /prorok add-evidence <event_id> --url \"<url>\" --direction indicator|counterindicator|neutral --summary \"<text>\"",
                file=sys.stderr,
            )
            return 2
        return run_python(code_dir / "prorok_evidence_cli.py", ["add-evidence", *rest], args.home)

    print(f"Unknown PROROK command: {subcommand}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
