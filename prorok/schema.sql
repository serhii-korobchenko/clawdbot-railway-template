-- PROROK forecasting system SQLite schema
-- Source of truth for events, assessments, evidence, source deduplication, and daily runs.
-- Runtime database file must be stored in Railway Volume and must not be committed to GitHub.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'resolved', 'archived')),
    forecast_horizon TEXT,
    decision_criteria TEXT,
    tags TEXT,
    source_image_note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL
        CHECK(run_type IN ('ingest', 'daily_update', 'manual_refresh', 'manual_cli', 'system')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running', 'completed', 'failed', 'partial')),
    events_processed INTEGER NOT NULL DEFAULT 0 CHECK(events_processed >= 0),
    new_sources_found INTEGER NOT NULL DEFAULT 0 CHECK(new_sources_found >= 0),
    model_used TEXT,
    errors TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    canonical_url_hash TEXT NOT NULL UNIQUE,
    title TEXT,
    domain TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source_type TEXT,
    raw_metadata TEXT
);

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    run_id INTEGER,
    direction TEXT NOT NULL
        CHECK(direction IN ('indicator', 'counterindicator', 'neutral')),
    strength TEXT
        CHECK(strength IN ('weak', 'medium', 'strong')),
    summary TEXT NOT NULL,
    relevance INTEGER CHECK(relevance BETWEEN 0 AND 100),
    credibility INTEGER CHECK(credibility BETWEEN 0 AND 100),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL,
    UNIQUE(event_id, source_id, direction, summary)
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    run_id INTEGER,
    assessed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    probability_percent INTEGER NOT NULL CHECK(probability_percent BETWEEN 0 AND 100),
    probability_band TEXT NOT NULL,
    probability_label TEXT NOT NULL,
    confidence TEXT CHECK(confidence IN ('low', 'medium', 'high')),
    delta_from_previous INTEGER,
    rationale TEXT,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS refresh_runs (
    refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    mode TEXT NOT NULL DEFAULT 'dry_run'
        CHECK(mode IN ('dry_run', 'apply')),
    trigger_source TEXT NOT NULL
        CHECK(trigger_source IN ('scheduled', 'telegram', 'manual_cli', 'system')),
    scope TEXT NOT NULL DEFAULT 'all'
        CHECK(scope IN ('all', 'event')),
    target_event_id TEXT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running', 'completed', 'failed', 'partial')),
    events_checked INTEGER NOT NULL DEFAULT 0 CHECK(events_checked >= 0),
    events_with_new_evidence INTEGER NOT NULL DEFAULT 0 CHECK(events_with_new_evidence >= 0),
    new_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(new_evidence_count >= 0),
    recommendations_count INTEGER NOT NULL DEFAULT 0 CHECK(recommendations_count >= 0),
    no_change_count INTEGER NOT NULL DEFAULT 0 CHECK(no_change_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
    model_used TEXT,
    summary TEXT,
    errors TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE SET NULL,
    FOREIGN KEY(target_event_id) REFERENCES events(event_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS refresh_event_results (
    refresh_event_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_id INTEGER NOT NULL,
    event_id TEXT,
    event_title_snapshot TEXT NOT NULL,
    baseline_assessment_id INTEGER,
    baseline_probability INTEGER CHECK(baseline_probability BETWEEN 0 AND 100),
    outcome TEXT NOT NULL
        CHECK(outcome IN ('new_evidence', 'no_new_evidence', 'error', 'skipped')),
    new_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(new_evidence_count >= 0),
    indicator_count INTEGER NOT NULL DEFAULT 0 CHECK(indicator_count >= 0),
    counterindicator_count INTEGER NOT NULL DEFAULT 0 CHECK(counterindicator_count >= 0),
    recommended_probability INTEGER CHECK(recommended_probability BETWEEN 0 AND 100),
    recommendation_confidence TEXT
        CHECK(recommendation_confidence IN ('low', 'medium', 'high')),
    change_recommended INTEGER NOT NULL DEFAULT 0 CHECK(change_recommended IN (0, 1)),
    recommendation_reason TEXT,
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(refresh_id) REFERENCES refresh_runs(refresh_id) ON DELETE CASCADE,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE SET NULL,
    FOREIGN KEY(baseline_assessment_id) REFERENCES assessments(assessment_id) ON DELETE SET NULL,
    UNIQUE(refresh_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_status
ON events(status);

CREATE INDEX IF NOT EXISTS idx_events_updated_at
ON events(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_runs_started_at
ON runs(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_sources_domain
ON sources(domain);

CREATE INDEX IF NOT EXISTS idx_sources_hash
ON sources(canonical_url_hash);

CREATE INDEX IF NOT EXISTS idx_evidence_event_time
ON evidence_items(event_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_evidence_source
ON evidence_items(source_id);

CREATE INDEX IF NOT EXISTS idx_assessments_event_time
ON assessments(event_id, assessed_at DESC, assessment_id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_finished
ON refresh_runs(finished_at DESC, refresh_id DESC);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_status
ON refresh_runs(status);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_refresh
ON refresh_event_results(refresh_id);

CREATE INDEX IF NOT EXISTS idx_refresh_event_results_event
ON refresh_event_results(event_id, refresh_id DESC);

CREATE VIEW IF NOT EXISTS latest_event_state AS
SELECT
    e.event_id,
    e.title,
    e.question,
    e.status,
    e.forecast_horizon,
    e.decision_criteria,
    e.tags,
    e.created_at,
    e.updated_at,
    a.assessment_id,
    a.assessed_at,
    a.probability_percent,
    a.probability_band,
    a.probability_label,
    a.confidence,
    a.delta_from_previous,
    a.rationale
FROM events e
LEFT JOIN assessments a
  ON a.assessment_id = (
      SELECT a2.assessment_id
      FROM assessments a2
      WHERE a2.event_id = e.event_id
      ORDER BY a2.assessed_at DESC, a2.assessment_id DESC
      LIMIT 1
  );

CREATE VIEW IF NOT EXISTS assessment_history AS
SELECT
    e.event_id,
    e.title,
    a.assessment_id,
    a.run_id,
    a.assessed_at,
    a.probability_percent,
    a.probability_band,
    a.probability_label,
    a.confidence,
    a.delta_from_previous,
    a.rationale
FROM assessments a
JOIN events e ON e.event_id = a.event_id;

CREATE VIEW IF NOT EXISTS event_evidence_summary AS
SELECT
    e.event_id,
    e.title,
    ei.direction,
    COUNT(ei.evidence_id) AS evidence_count,
    MAX(ei.created_at) AS last_evidence_at
FROM events e
LEFT JOIN evidence_items ei ON ei.event_id = e.event_id
GROUP BY e.event_id, e.title, ei.direction;

CREATE VIEW IF NOT EXISTS source_registry AS
SELECT
    s.source_id,
    s.domain,
    s.title,
    s.url,
    s.canonical_url,
    s.published_at,
    s.first_seen_at,
    s.last_seen_at,
    COUNT(ei.evidence_id) AS used_as_evidence_count
FROM sources s
LEFT JOIN evidence_items ei ON ei.source_id = s.source_id
GROUP BY s.source_id;

CREATE VIEW IF NOT EXISTS daily_run_summary AS
SELECT
    run_id,
    run_type,
    started_at,
    finished_at,
    status,
    events_processed,
    new_sources_found,
    model_used,
    errors,
    notes
FROM runs;

CREATE TRIGGER IF NOT EXISTS trg_events_updated_at
AFTER UPDATE ON events
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE events
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE event_id = NEW.event_id;
END;

INSERT INTO meta(key, value)
VALUES
    ('schema_version', '2'),
    ('probability_scale', '0-5%: Віддалена можливість
10-20%: Низька ймовірність
25-35%: Малоймовірно
40-50%: Реалістична можливість
55-75%: Ймовірно
80-90%: Висока ймовірність
95-100%: Майже напевно')
ON CONFLICT(key) DO UPDATE SET
    value = excluded.value,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');
