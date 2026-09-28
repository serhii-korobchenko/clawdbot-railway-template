#!/usr/bin/env python3
"""Read-only post-run freshness audit for an OpenClaw PROROK session JSONL.

This is a standalone diagnostic for cron runs outside refresh_event_results.
The tracked collector already enforces the full v8 gate. No database writes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import prorok_refresh_collector_v6 as v6


def check_freshness(session_path: Path) -> tuple[bool, str, int]:
    searches, results, verifications = v6._extract_trace(session_path)
    core = searches[:3]
    if len(core) != 3:
        return False, f"expected 3 search calls; observed {len(core)}", 0
    if any(call["tool"] != "tavily_search" for call in core):
        return False, "first three searches must use tavily_search", 0
    targets, errors = v6._undated_verification_targets(core, results)
    if errors:
        return False, "; ".join(errors), len(targets)
    verified_after: dict[str, int] = {}
    for call in verifications:
        sequence = int(call["sequence"])
        for url in v6._verification_urls(call["args"]):
            verified_after[url] = max(verified_after.get(url, 0), sequence)
    missing = sorted(url for url, boundary in targets.items()
                     if verified_after.get(url, 0) <= boundary)
    if missing:
        # Avoid exposing source URLs in routine diagnostic output.
        return False, f"missing freshness verification for {len(missing)} required URL(s)", len(targets)
    return True, "all required undated URLs verified after search", len(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_jsonl", type=Path)
    args = parser.parse_args(argv)
    try:
        valid, reason, required = check_freshness(args.session_jsonl)
    except (OSError, ValueError) as exc:
        print(f"freshness_check: FAIL; unreadable transcript: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"freshness_check: {'PASS' if valid else 'FAIL'}")
    print(f"required_unique_urls: {required}")
    print(f"reason: {reason}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
