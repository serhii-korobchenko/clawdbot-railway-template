# PROROK refresh dry-run launcher

`prorok_refresh_dry_run_cron.py` creates a one-shot OpenClaw cron job that performs a no-write web refresh for a selected PROROK event.

The launcher reads the current event state from the PROROK SQLite database, including the latest assessment and recent evidence rows, then builds a structured prompt for the agent. The prompt explicitly forbids database writes and asks the agent to return candidate evidence and an assessment recommendation only.

## Safety rule

This workflow is intentionally review-first:

```text
web refresh -> candidate evidence -> human review -> /prorok add-evidence and /prorok assess
```

The launcher does **not** call `/prorok add-evidence` or `/prorok assess`, and the prompt tells the agent not to call them either.

## Telegram command

The deterministic PROROK router exposes the refresh dry-run as a Telegram/OpenClaw command:

```text
/prorok refresh Nuclear_threat
```

Optional flags can be forwarded to the launcher:

```text
/prorok refresh Nuclear_threat --thread-id 112 --at 2m
/prorok refresh Nuclear_threat --no-schedule
```

The command creates a one-shot cron job and sends the final dry-run report to the configured Telegram topic. It does not write anything to the PROROK database.

## Typical Railway command

```bash
python3 /app/prorok/prorok_refresh_dry_run_cron.py Nuclear_threat \
  --to -1003804919781 \
  --thread-id 112 \
  --at 2m
```

The result should appear in the Telegram topic as a report beginning with:

```text
PROROK_REFRESH_DRY_RUN
event_id: Nuclear_threat
```

## Prompt-only mode

To generate the prompt without creating a cron job:

```bash
python3 /app/prorok/prorok_refresh_dry_run_cron.py Nuclear_threat --no-schedule
```

The generated prompt is written to:

```text
/data/workspace/prorok/refresh_prompts/
```

## Default runtime values

```text
DB path: /data/workspace/prorok/prorok.sqlite3
Telegram chat: -1003804919781
Telegram thread: 112
One-shot schedule: 2m
Tools: tavily_search tavily_extract web_search web_fetch read
Timeout: 300 seconds
```
