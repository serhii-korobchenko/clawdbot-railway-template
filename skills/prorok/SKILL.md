---
name: prorok
description: >
  PROROK forecasting skill for Railway/OpenClaw. Stores and reads forecast events,
  evidence, source registry entries, and probability assessments from the PROROK
  SQLite database in the Railway Volume. Use for /prorok commands, forecast-event
  tracking, probability trends, evidence lists, source deduplication checks, and
  PROROK health checks.
user-invocable: true
command-dispatch: tool
command-tool: prorok_query
metadata:
  openclaw:
    always: true
    requires:
      bins:
        - python3
---

# PROROK Forecasting Skill

This skill exposes the local PROROK forecasting database and CLI helpers to OpenClaw.

PROROK runtime data is stored in SQLite at:

```text
/data/workspace/prorok/prorok.sqlite3
```

## Critical execution rule

For every user-facing `/prorok ...` command, use the deterministic local tool dispatch first.

Use this tool name exactly:

```text
prorok_query
```

Pass the full original user command as the tool input, for example:

```text
/prorok list
/prorok show ru_capture_ukraine_oblast_center_2026
/prorok trend ru_capture_ukraine_oblast_center_2026
/prorok evidence ru_capture_ukraine_oblast_center_2026
/prorok sources
/prorok health
```

Do not answer `/prorok ...` commands from memory. Do not use web search for `/prorok list`, `/prorok show`, `/prorok trend`, `/prorok evidence`, `/prorok sources`, or `/prorok health`.

Do not manually query SQLite with `sqlite3`. Do not choose a helper script yourself for command-style requests. The runtime fallback for `prorok_query` calls:

```bash
python3 /app/prorok/prorok_router.py "<FULL_ORIGINAL_PROROK_COMMAND>"
```

This rule is important because different PROROK subcommands are handled by different helper scripts:

- event state: `prorok_event_cli.py`
- assessment history: `prorok_assessment_cli.py`
- evidence and sources: `prorok_evidence_cli.py`
- health: `prorok_cli.py`

Never run `prorok_event_cli.py` for `/prorok trend`, `/prorok evidence`, or `/prorok sources`.

## Supported user commands

Use `prorok_query` when the user asks for:

- `/prorok list`
- `/prorok show <event_id>`
- `/prorok trend <event_id>`
- `/prorok evidence <event_id>`
- `/prorok sources`
- `/prorok health`
- current PROROK event state
- probability history or trend
- evidence, indicators, counterindicators, source registry, or source deduplication

Return the meaningful `prorok_query` output to the user. Keep Telegram replies concise by default.

## Runtime path rule

In Railway deployment, PROROK code is located at:

```text
/app/prorok
```

The deterministic router is:

```text
/app/prorok/prorok_router.py
```

Always use the Railway Volume DB home:

```text
/data/workspace/prorok
```

## Router command mapping

The router maps user-facing commands as follows:

| User command | Router dispatch |
|---|---|
| `/prorok health` | `prorok_cli.py --home /data/workspace/prorok health` |
| `/prorok list` | `prorok_event_cli.py --home /data/workspace/prorok list-events --status all` |
| `/prorok show <event_id>` | `prorok_event_cli.py --home /data/workspace/prorok show-event <event_id>` |
| `/prorok trend <event_id>` | `prorok_assessment_cli.py --home /data/workspace/prorok trend <event_id>` |
| `/prorok evidence <event_id>` | `prorok_evidence_cli.py --home /data/workspace/prorok evidence <event_id> --show-canonical` |
| `/prorok sources` | `prorok_evidence_cli.py --home /data/workspace/prorok sources --show-canonical` |

## Adding events

For manual event creation requests, prefer direct CLI only when the user explicitly asks to add or update a forecast event.

```bash
python3 /app/prorok/prorok_event_cli.py --home /data/workspace/prorok add-event \
  --event-id "<stable_event_id>" \
  --title "<title>" \
  --question "<forecast_question>" \
  --forecast-horizon "<YYYY-MM-DD>" \
  --criteria "<decision_criteria>" \
  --tags "<comma,separated,tags>" \
  --source-image-note "<source note>"
```

Use a stable ASCII `event_id` such as `ukraine_ceasefire_2026` or `ru_capture_oblast_center_2026`. Do not create duplicate events for the same forecast question. If unsure, run `/prorok list` first through `prorok_query`.

## Adding evidence

For evidence attachment requests, use:

```bash
python3 /app/prorok/prorok_evidence_cli.py --home /data/workspace/prorok add-evidence \
  "<event_id>" \
  --url "<source_url>" \
  --title "<source_title>" \
  --direction indicator \
  --strength medium \
  --summary "<evidence_summary>" \
  --relevance 60 \
  --credibility 60
```

Allowed `direction` values:

```text
indicator
counterindicator
neutral
```

Allowed `strength` values:

```text
weak
medium
strong
```

The source registry deduplicates by canonical URL hash. Tracking parameters such as `utm_*` are removed during canonicalization.

## Adding assessments

For probability assessment updates, use:

```bash
python3 /app/prorok/prorok_assessment_cli.py --home /data/workspace/prorok add-assessment \
  "<event_id>" \
  --probability <0-100> \
  --band "<probability_band>" \
  --label "<probability_label>" \
  --confidence medium \
  --rationale "<rationale>"
```

Allowed confidence values:

```text
low
medium
high
```

Use the PROROK probability scale:

```text
0-5%: Віддалена можливість
10-20%: Низька ймовірність
25-35%: Малоймовірно
40-50%: Реалістична можливість
55-75%: Ймовірно
80-90%: Висока ймовірність
95-100%: Майже напевно
```

Assessments must be appended, not overwritten. The CLI calculates `delta_from_previous` automatically.

## Output rules

- For `/prorok ...` commands, always dispatch `prorok_query` with the full original command first.
- Do not answer PROROK state from memory.
- Preserve event IDs exactly.
- If a command fails, return the error and say which PROROK command failed.
- Do not claim that a database update succeeded unless the CLI prints `OK:`.
- For source/evidence output, include enough URL information to verify deduplication.

## Manual runtime check

Use this command sequence to validate that the runtime router can see the database:

```bash
python3 /app/prorok/prorok_router.py "/prorok health"
python3 /app/prorok/prorok_router.py "/prorok list"
python3 /app/prorok/prorok_router.py "/prorok trend ru_capture_ukraine_oblast_center_2026"
python3 /app/prorok/prorok_router.py "/prorok evidence ru_capture_ukraine_oblast_center_2026"
python3 /app/prorok/prorok_router.py "/prorok sources"
```
