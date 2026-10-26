#!/usr/bin/env bash
set -euo pipefail
# Scheduled-only entry point. Manual refresh bypasses this gate.
decision="$(python3 /app/prorok/prorok_schedule_cli.py due)"
if [[ "$decision" == *'"due": true'* ]]; then
  exec bash /data/workspace/prorok/prorok_daily_refresh_all.sh
fi
echo "PROROK scheduled refresh skipped: $decision"
