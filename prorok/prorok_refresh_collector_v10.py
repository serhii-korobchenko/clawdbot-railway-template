#!/usr/bin/env python3
"""PROROK collector v10: v9 collection plus Telegram batch completion notifier."""

from __future__ import annotations

import sys
from pathlib import Path

import prorok_refresh_collector_v9 as v9
from prorok_refresh_notifier import notify_completed_telegram_refreshes

COLLECTOR_VERSION="10"
base=v9.base

def collect_once_v10(db: Path, state_dir: Path, limit: int=100) -> dict[str,int]:
    counts=dict(v9.collect_once_v9(db,state_dir,limit))
    notice=notify_completed_telegram_refreshes(db)
    for key,value in notice.items():
        if value:
            counts[f"notification:{key}"]=value
    return counts

def main(argv: list[str] | None=None) -> int:
    base.COLLECTOR_VERSION=COLLECTOR_VERSION
    base.collect_one=v9.v8.collect_one_v8
    base.collect_once=collect_once_v10
    return base.main(argv)

if __name__=="__main__":
    raise SystemExit(main(sys.argv[1:]))
