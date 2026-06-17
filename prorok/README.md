# PROROK — agentic forecasting storage layer

`PROROK` is an experimental OpenClaw/Railway subsystem for storing forecast events, evidence, source references, and probability assessments. The current implementation is intentionally small and operational: it uses SQLite in the Railway Volume and a set of dependency-free Python CLI helpers.

The goal of this first stage is to create a reliable data foundation before connecting Telegram screenshot intake, daily web search, cron runs, and dashboard views.

## Runtime location

The persistent runtime database is stored in the Railway Volume:

```text
/data/workspace/prorok/prorok.sqlite3
```

The schema and helper scripts are stored in the repository under:

```text
prorok/
```

The runtime database itself is not committed to GitHub.

## Pipeline

The minimum validated pipeline is:

```text
event -> evidence/source -> assessment -> latest/trend/list
```

Meaning:

1. A forecast event is created from a Telegram screenshot or another intake source.
2. Evidence items are attached to the event as indicators, counterindicators, or neutral observations.
3. Sources are deduplicated by canonical URL hash.
4. Probability assessments are appended to the event history.
5. Views and CLI commands expose current state and historical trend.

## Database objects

Main tables:

```text
events
author assessments
sources
evidence_items
runs
meta
```

Views for operational use and future dashboard:

```text
latest_event_state
assessment_history
source_registry
event_evidence_summary
daily_run_summary
```

## Probability scale

The probability scale is stored in `meta` and currently follows:

```text
0-5%: Віддалена можливість
10-20%: Низька ймовірність
25-35%: Малоймовірно
40-50%: Реалістична можливість
55-75%: Ймовірно
80-90%: Висока ймовірність
95-100%: Майже напевно
```

## CLI files

### `prorok_init_db.py`

Initializes the SQLite database from `schema.sql`.

Typical command:

```bash
python3 prorok/prorok_init_db.py --home /data/workspace/prorok
```

### `prorok_event_cli.py`

Adds, updates, lists, and displays events.

Commands:

```text
add-event
list-events
show-event
```

Example:

```bash
python3 prorok/prorok_event_cli.py --home /data/workspace/prorok add-event \
  --event-id "test_generic_event_2026" \
  --title "Тестова generic-подія PROROK" \
  --question "Чи буде ця тестова подія коректно збережена у SQLite?" \
  --forecast-horizon "2026-12-31" \
  --criteria "Позитивне вирішення: подія збережена, доступна через list-events і show-event." \
  --tags "test,prorok,sqlite,2026" \
  --source-image-note "Manual CLI test instead of Telegram screenshot."
```

### `prorok_evidence_cli.py`

Adds and lists evidence and source registry entries.

Commands:

```text
add-evidence
sources
evidence
```

Example:

```bash
python3 prorok/prorok_evidence_cli.py --home /data/workspace/prorok add-evidence \
  test_generic_event_2026 \
  --url "https://example.com/generic-event-evidence?utm_source=cli_test" \
  --title "Generic event evidence source" \
  --direction indicator \
  --strength medium \
  --summary "Тестовий індикатор для generic-події." \
  --relevance 65 \
  --credibility 55
```

### `prorok_assessment_cli.py`

Adds and reads probability assessments.

Commands:

```text
add-assessment
latest
trend
```

Example:

```bash
python3 prorok/prorok_assessment_cli.py --home /data/workspace/prorok add-assessment \
  test_generic_event_2026 \
  --probability 80 \
  --band "80-90%" \
  --label "Висока ймовірність" \
  --confidence high \
  --rationale "Тестова оцінка generic-події: подія створена, evidence додано, show/list працюють."
```

### `prorok_cli.py`

General operational helper for health checks, initial test event, event list, event card, and assessment trend.

Commands:

```text
health
list
show <event_id>
trend <event_id>
add-test-event
```

## Source deduplication rule

Sources are deduplicated by `canonical_url_hash`.

Canonicalization currently:

- lowercases URL scheme and host;
- removes fragments;
- removes common tracking query parameters such as `utm_*`, `fbclid`, `gclid`, `gbraid`, `wbraid`, `mc_cid`, `mc_eid`, `igshid`, `ref`, `ref_src`;
- strips a trailing slash from non-root paths;
- hashes the canonical URL with SHA256.

Duplicate sources do not create new `sources` records. After the duplicate-source metadata patch, duplicate URLs also do not overwrite the original `url`, `title`, `published_at`, or `first_seen_at`; they only refresh `last_seen_at` and fill selected empty service fields.

## Validated runtime checks

The following checks were validated in Railway shell:

```text
1. DB initialization in /data/workspace/prorok
2. CLI syntax checks with py_compile
3. Event creation and update
4. Evidence insertion
5. Source URL deduplication with tracking parameters
6. Duplicate source metadata preservation
7. Assessment history with delta_from_previous
8. Full pipeline on a generic event
```

Validated example state:

```text
test_generic_event_2026
latest: 80% — Висока ймовірність
evidence rows: 1
trend rows: 1
```

Validated example event:

```text
ru_capture_ukraine_oblast_center_2026
latest: 35% — Малоймовірно
assessment history: 25% -> 40% -> 35%
```

## Operational notes

The Railway `/app` directory may not be a Git working tree. For runtime testing, use a temporary clone:

```bash
rm -rf /tmp/prorok-agent-system
git clone --depth 1 --branch feature/prorok-agent-system \
  https://github.com/serhii-korobchenko/clawdbot-railway-template.git \
  /tmp/prorok-agent-system
cd /tmp/prorok-agent-system
```

To refresh the temporary clone:

```bash
cd /tmp/prorok-agent-system
git fetch origin feature/prorok-agent-system
git reset --hard origin/feature/prorok-agent-system
```

## Next planned stages

1. Telegram command wiring:
   - `/prorok list`
   - `/prorok show <event_id>`
   - `/prorok trend <event_id>`
   - `/prorok evidence <event_id>`
   - `/prorok refresh <event_id>`

2. Screenshot intake:
   - detect Telegram image messages;
   - extract title, question, forecast horizon, criteria, and tags;
   - write extracted event to SQLite with `prorok_event_cli.py` logic or equivalent Python module.

3. Daily web-search workflow:
   - iterate active events;
   - search for indicators and counterindicators;
   - canonicalize and deduplicate sources;
   - add evidence;
   - create a new probability assessment.

4. Cron integration:
   - daily run through OpenClaw cron;
   - write a run record to `runs`;
   - send Telegram summary.

5. Dashboard/API layer:
   - read from SQLite views;
   - display current probabilities, trend charts, source registry, evidence split, and daily run status.

## Current branch

Feature branch:

```text
feature/prorok-agent-system
```
