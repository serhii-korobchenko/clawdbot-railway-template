---
name: prorok
description: >
  PROROK forecasting skill for Railway/OpenClaw. Stores and reads forecast events,
  evidence, source registry entries, and probability assessments from the PROROK
  SQLite database in the Railway Volume. Use for /prorok commands, forecast-event
  tracking, probability trends, evidence lists, source deduplication checks, and
  PROROK health checks.
user-invocable: true
metadata:
  openclaw:
    always: true
    requires:
      bins:
        - python3
---

# PROROK Forecasting Skill

This skill exposes the local PROROK forecasting database and CLI helpers to OpenClaw native skill commands.

PROROK is stored in SQLite at:

```text
/data/workspace/prorok/prorok.sqlite3
```

Use this skill when the user asks for:

- `/prorok list`
- `/prorok show <event_id>`
- `/prorok trend <event_id>`
- `/prorok evidence <event_id>`
- `/prorok sources`
- `/prorok health`
- current PROROK event state
- probability history or trend
- evidence, indicators, counterindicators, source registry, or source deduplication
- adding or updating PROROK forecast events, evidence, or assessments

## Runtime path rule

Before running any PROROK command, determine the script directory.

Use `/app/prorok` in Railway deployment:

```bash
PROROK_CODE_DIR=/app/prorok
```

If `/app/prorok` does not exist during manual runtime tests, use the temporary clone:

```bash
PROROK_CODE_DIR=/tmp/prorok-agent-system/prorok
```

If neither directory exists, report that PROROK code is not available in the runtime container.

Always use the Railway Volume DB home:

```bash
PROROK_HOME=/data/workspace/prorok
```

## Command mapping

### Health

For `/prorok health` or PROROK status checks, run:

```bash
python3 "$PROROK_CODE_DIR/prorok_cli.py" --home "$PROROK_HOME" health
```

Return the DB path, counts, and schema health summary.

### List events

For `/prorok list`, run:

```bash
python3 "$PROROK_CODE_DIR/prorok_event_cli.py" --home "$PROROK_HOME" list-events --status all
```

Return event title, event_id, status, forecast horizon, latest probability, confidence, and assessment time.

### Show one event

For `/prorok show <event_id>`, run:

```bash
python3 "$PROROK_CODE_DIR/prorok_event_cli.py" --home "$PROROK_HOME" show-event "<event_id>"
```

Return the event card and latest assessment. If the event is not found, say so clearly and suggest `/prorok list`.

### Assessment trend

For `/prorok trend <event_id>`, run:

```bash
python3 "$PROROK_CODE_DIR/prorok_assessment_cli.py" --home "$PROROK_HOME" trend "<event_id>"
```

Return assessment history with probability, label, confidence, delta_from_previous, and rationale.

### Evidence for one event

For `/prorok evidence <event_id>`, run:

```bash
python3 "$PROROK_CODE_DIR/prorok_evidence_cli.py" --home "$PROROK_HOME" evidence "<event_id>" --show-canonical
```

Return evidence rows grouped by indicator/counterindicator/neutral when useful. Preserve source URLs and canonical URLs.

### Source registry

For `/prorok sources`, run:

```bash
python3 "$PROROK_CODE_DIR/prorok_evidence_cli.py" --home "$PROROK_HOME" sources --show-canonical
```

Return source_id, domain, used count, title, URL, and canonical URL.

## Adding events

When the user asks to add a forecast event manually, use:

```bash
python3 "$PROROK_CODE_DIR/prorok_event_cli.py" --home "$PROROK_HOME" add-event \
  --event-id "<stable_event_id>" \
  --title "<title>" \
  --question "<forecast_question>" \
  --forecast-horizon "<YYYY-MM-DD>" \
  --criteria "<decision_criteria>" \
  --tags "<comma,separated,tags>" \
  --source-image-note "<source note>"
```

Use a stable ASCII `event_id` such as `ukraine_ceasefire_2026` or `ru_capture_oblast_center_2026`. Do not create duplicate events for the same forecast question. If unsure, run `/prorok list` first.

## Adding evidence

When the user asks to attach evidence to an event, use:

```bash
python3 "$PROROK_CODE_DIR/prorok_evidence_cli.py" --home "$PROROK_HOME" add-evidence \
  "<event_id>" \
  --url "<source_url>" \
  --title "<source_title>" \
  --direction indicator \
  --strength medium \
  --summary "<evidence_summary>" \
  --relevance 60 \
  --credibility 60
```

Allowed `direction` values are:

```text
indicator
counterindicator
neutral
```

Allowed `strength` values are:

```text
weak
medium
strong
```

The source registry deduplicates by canonical URL hash. Tracking parameters such as `utm_*` are removed during canonicalization.

## Adding assessments

When the user asks to add or update a probability assessment, use:

```bash
python3 "$PROROK_CODE_DIR/prorok_assessment_cli.py" --home "$PROROK_HOME" add-assessment \
  "<event_id>" \
  --probability <0-100> \
  --band "<probability_band>" \
  --label "<probability_label>" \
  --confidence medium \
  --rationale "<rationale>"
```

Allowed confidence values are:

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

- Do not answer PROROK state from memory.
- Always run the relevant CLI command.
- Keep Telegram replies concise by default.
- Preserve event IDs exactly.
- If a command fails, return the error and the command category that failed.
- Do not claim that a database update succeeded unless the CLI prints `OK:`.
- For source/evidence output, include enough URL information to verify deduplication.

## Manual runtime check

Use this command sequence to validate that the skill can see the runtime database:

```bash
if [ -d /app/prorok ]; then PROROK_CODE_DIR=/app/prorok; else PROROK_CODE_DIR=/tmp/prorok-agent-system/prorok; fi
PROROK_HOME=/data/workspace/prorok
python3 "$PROROK_CODE_DIR/prorok_cli.py" --home "$PROROK_HOME" health
python3 "$PROROK_CODE_DIR/prorok_event_cli.py" --home "$PROROK_HOME" list-events --status all
```
