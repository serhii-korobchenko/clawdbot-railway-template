from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from prorok_api.app import create_app
from prorok_api.config import ApiSettings


TEST_TOKEN = "test-api-token"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "prorok-test.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            question TEXT NOT NULL,
            status TEXT NOT NULL,
            forecast_horizon TEXT,
            decision_criteria TEXT,
            tags TEXT,
            source_image_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE runs (
            run_id INTEGER PRIMARY KEY,
            run_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            events_processed INTEGER NOT NULL DEFAULT 0,
            new_sources_found INTEGER NOT NULL DEFAULT 0,
            model_used TEXT,
            errors TEXT,
            notes TEXT
        );

        CREATE TABLE sources (
            source_id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            canonical_url_hash TEXT NOT NULL UNIQUE,
            title TEXT,
            domain TEXT,
            published_at TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source_type TEXT,
            raw_metadata TEXT
        );

        CREATE TABLE assessments (
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            run_id INTEGER,
            assessed_at TEXT NOT NULL,
            probability_percent INTEGER NOT NULL,
            probability_band TEXT NOT NULL,
            probability_label TEXT NOT NULL,
            confidence TEXT,
            delta_from_previous INTEGER,
            rationale TEXT
        );

        CREATE TABLE evidence_items (
            evidence_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            run_id INTEGER,
            direction TEXT NOT NULL,
            strength TEXT,
            summary TEXT NOT NULL,
            relevance INTEGER,
            credibility INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE refresh_event_results (
            refresh_event_result_id INTEGER PRIMARY KEY,
            refresh_id INTEGER NOT NULL,
            event_id TEXT,
            event_title_snapshot TEXT NOT NULL,
            baseline_assessment_id INTEGER,
            baseline_probability INTEGER,
            job_state TEXT NOT NULL,
            outcome TEXT,
            recommended_probability INTEGER,
            recommended_band TEXT,
            recommended_label TEXT,
            recommendation_confidence TEXT,
            recommendation_reason TEXT,
            change_recommended INTEGER NOT NULL DEFAULT 0,
            candidate_rejected_count INTEGER NOT NULL DEFAULT 0,
            recommendation_valid INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE refresh_user_decisions (
            decision_id INTEGER PRIMARY KEY,
            refresh_event_result_id INTEGER NOT NULL UNIQUE,
            event_id_snapshot TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            baseline_assessment_id INTEGER,
            baseline_probability INTEGER NOT NULL,
            recommended_probability INTEGER,
            selected_probability INTEGER NOT NULL,
            assessment_id INTEGER,
            decision_source TEXT NOT NULL,
            decided_at TEXT NOT NULL
        );

        CREATE VIEW latest_event_state AS
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
        """
    )

    conn.executemany(
        """
        INSERT INTO events(
            event_id, title, question, status, forecast_horizon,
            decision_criteria, tags, source_image_note,
            created_at, updated_at, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "active_event",
                "Nuclear test event",
                "Will the nuclear test event occur?",
                "active",
                "2026-12-31",
                '{"event_occurs_if":["confirmed"]}',
                '["nuclear","test"]',
                "screenshot-note",
                "2026-01-01T00:00:00Z",
                "2026-06-01T00:00:00Z",
                None,
            ),
            (
                "archived_empty",
                "Archived legacy event",
                "Was this archived?",
                "archived",
                "2026-12-31",
                "Legacy criteria text",
                '["legacy"]',
                None,
                "2026-01-02T00:00:00Z",
                "2026-06-02T00:00:00Z",
                None,
            ),
        ],
    )
    conn.execute(
        """
        INSERT INTO assessments(
            assessment_id, event_id, run_id, assessed_at,
            probability_percent, probability_band, probability_label,
            confidence, delta_from_previous, rationale
        ) VALUES (1, 'active_event', 10, '2026-06-01T10:00:00Z',
                  35, '25-35%', 'Малоймовірно', 'medium', -5, 'Because.')
        """
    )
    conn.execute(
        """
        INSERT INTO sources(
            source_id, url, canonical_url, canonical_url_hash,
            title, domain, published_at, first_seen_at, last_seen_at,
            source_type, raw_metadata
        ) VALUES (
            1, 'https://example.com/a', 'https://example.com/a', 'hash1',
            'Example', 'example.com', NULL,
            '2026-06-01T09:00:00Z', '2026-06-01T09:00:00Z',
            'web', '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO evidence_items(
            evidence_id, event_id, source_id, run_id, direction,
            strength, summary, relevance, credibility, created_at
        ) VALUES (
            1, 'active_event', 1, 11, 'indicator',
            'medium', 'Evidence summary', 80, 90,
            '2026-06-01T09:30:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_event_result_id,
            refresh_id,
            event_id,
            event_title_snapshot,
            baseline_assessment_id,
            baseline_probability,
            job_state,
            outcome,
            recommended_probability,
            recommended_band,
            recommended_label,
            recommendation_confidence,
            recommendation_reason,
            change_recommended,
            candidate_rejected_count,
            recommendation_valid,
            created_at
        ) VALUES (
            100,
            50,
            'active_event',
            'Nuclear test event',
            1,
            35,
            'completed',
            'new_evidence',
            45,
            '40-50%',
            'Реалістична можливість',
            'medium',
            'New evidence supports an increase.',
            1,
            0,
            1,
            '2026-06-02T12:00:00Z'
        )
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def api_settings(db_path):
    return ApiSettings(
        db_path=str(db_path),
        api_token=TEST_TOKEN,
    )


@pytest.fixture
def client(api_settings):
    app = create_app(api_settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
