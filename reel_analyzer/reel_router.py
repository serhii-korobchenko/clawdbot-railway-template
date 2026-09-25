#!/usr/bin/env python3
"""Deterministic entry point used by the OpenClaw Reel Analyzer skill."""

from __future__ import annotations

from reel_analyzer.reel_analyzer import main

if __name__ == "__main__":
    raise SystemExit(main())
