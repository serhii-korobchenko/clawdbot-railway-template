from __future__ import annotations

import sqlite3
from datetime import timezone
from types import SimpleNamespace

import pytest

from prorok.prorok_evidence_cli import canonicalize_url
from prorok.prorok_refresh_collector import (
    resolve_search_boundary_datetime,
    validate_candidates,
)
from prorok.prorok_refresh_decision_cli import (
    CliError,
    assert_candidate_source_not_already_official,
)
from prorok.prorok_refresh_dry_run_cron import (
    AssessmentState,
    EventState,
    build_prompt,
    load_search_after_at,
)
from prorok.prorok_refresh_search_protocol import _build_search_protocol


def make_boundary_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            assessed_at TEXT NOT NULL
        );

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            baseline_assessment_id INTEGER,
            job_state TEXT,
            cron_status TEXT,
            cron_run_at_ms INTEGER,
            outcome TEXT,
            search_quality_valid INTEGER
        );

        CREATE TABLE refresh_user_decisions(
            decision_id INTEGER PRIMARY KEY,
            refresh_event_result_id INTEGER NOT NULL,
            event_id_snapshot TEXT NOT NULL,
            decided_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO assessments VALUES(16, 'event_a', '2026-09-09T21:03:01Z')"
    )
    conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_event_result_id,event_id,baseline_assessment_id,
            job_state,cron_status,cron_run_at_ms,outcome,search_quality_valid
        )
        VALUES(107,'event_a',16,'completed','ok',1789724943233,'new_evidence',1)
        """
    )
    conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_event_result_id,event_id,baseline_assessment_id,
            job_state,cron_status,cron_run_at_ms,outcome,search_quality_valid
        )
        VALUES(110,'event_a',16,'completed','ok',1789733889249,'new_evidence',1)
        """
    )
    conn.execute(
        """
        INSERT INTO refresh_user_decisions(
            decision_id,refresh_event_result_id,event_id_snapshot,decided_at
        )
        VALUES(2,107,'event_a','2026-09-18T11:43:00Z')
        """
    )
    conn.commit()
    return conn


def test_finalized_refresh_run_start_advances_search_boundary() -> None:
    conn = make_boundary_db()
    try:
        boundary = load_search_after_at(
            conn,
            "event_a",
            "2026-09-09T21:03:01Z",
        )
        assert boundary == "2026-09-18T09:49:03.233Z"

        row = conn.execute(
            "SELECT * FROM refresh_event_results WHERE refresh_event_result_id = 110"
        ).fetchone()
        resolved = resolve_search_boundary_datetime(conn, row)
        assert resolved.tzinfo == timezone.utc
        assert resolved.isoformat() == "2026-09-18T09:49:03.233000+00:00"
    finally:
        conn.close()


def test_valid_no_new_evidence_advances_search_boundary_without_decision() -> None:
    conn = make_boundary_db()
    try:
        conn.execute(
            """
            INSERT INTO refresh_event_results(
                refresh_event_result_id,event_id,baseline_assessment_id,
                job_state,cron_status,cron_run_at_ms,outcome,search_quality_valid
            )
            VALUES(
                111,'event_a',16,'completed','ok',1789745443321,
                'no_new_evidence',1
            )
            """
        )
        conn.commit()

        boundary = load_search_after_at(
            conn,
            "event_a",
            "2026-09-09T21:03:01Z",
        )
        assert boundary == "2026-09-18T15:30:43.321Z"

        row = conn.execute(
            "SELECT * FROM refresh_event_results WHERE refresh_event_result_id = 111"
        ).fetchone()
        resolved = resolve_search_boundary_datetime(conn, row)
        assert resolved.isoformat() == "2026-09-18T15:30:43.321000+00:00"
    finally:
        conn.close()


def test_undecided_new_evidence_does_not_advance_search_boundary() -> None:
    conn = make_boundary_db()
    try:
        conn.execute("DELETE FROM refresh_user_decisions")
        conn.commit()

        boundary = load_search_after_at(
            conn,
            "event_a",
            "2026-09-09T21:03:01Z",
        )
        assert boundary == "2026-09-09T21:03:01Z"

        row = conn.execute(
            "SELECT * FROM refresh_event_results WHERE refresh_event_result_id = 110"
        ).fetchone()
        resolved = resolve_search_boundary_datetime(conn, row)
        assert resolved.isoformat() == "2026-09-09T21:03:01+00:00"
    finally:
        conn.close()


def test_search_boundary_falls_back_to_assessment_when_no_safe_refresh_exists() -> None:
    conn = make_boundary_db()
    try:
        conn.execute("DELETE FROM refresh_user_decisions")
        conn.execute("DELETE FROM refresh_event_results")
        conn.execute(
            """
            INSERT INTO refresh_event_results(
                refresh_event_result_id,event_id,baseline_assessment_id,
                job_state,cron_status,cron_run_at_ms,outcome,search_quality_valid
            )
            VALUES(200,'event_a',16,'scheduled',NULL,NULL,NULL,NULL)
            """
        )
        conn.commit()

        boundary = load_search_after_at(
            conn,
            "event_a",
            "2026-09-09T21:03:01Z",
        )
        assert boundary == "2026-09-09T21:03:01Z"

        row = conn.execute(
            "SELECT * FROM refresh_event_results WHERE refresh_event_result_id = 200"
        ).fetchone()
        resolved = resolve_search_boundary_datetime(conn, row)
        assert resolved.isoformat() == "2026-09-09T21:03:01+00:00"
    finally:
        conn.close()


def test_search_protocol_prefers_search_after_at_and_keeps_legacy_fallback() -> None:
    prompt = (
        "last_assessed_at: 2026-09-09T21:03:01Z\n"
        "search_after_at: 2026-09-18T09:49:03.233Z\n"
    )
    protocol = _build_search_protocol(prompt)
    assert "точна межа search_after_at = 2026-09-18T09:49:03.233000Z" in protocol
    assert "site:" not in protocol

    legacy = _build_search_protocol(
        "last_assessed_at: 2026-09-09T21:03:01Z\n"
    )
    assert "2026-09-09T21:03:01Z" in legacy


def test_prompt_exposes_search_after_at_separately_from_assessment_time() -> None:
    event = EventState(
        event_id="event_a",
        title="Event A",
        question="Will A happen?",
        forecast_horizon="2026-12-31",
        status="active",
        decision_criteria="criterion",
        tags="test",
    )
    latest = AssessmentState(
        probability="70%",
        band="55-75%",
        label="Ймовірно",
        confidence="medium",
        assessed_at="2026-09-09T21:03:01Z",
        rationale="baseline",
    )
    prompt = build_prompt(
        event,
        latest,
        ["No evidence rows yet."],
        "2026-09-18T09:49:03.233Z",
    )
    assert "last_assessed_at: 2026-09-09T21:03:01Z" in prompt
    assert "search_after_at: 2026-09-18T09:49:03.233Z" in prompt
    assert "після search_after_at" in prompt


def make_dedup_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            assessed_at TEXT NOT NULL
        );

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            baseline_assessment_id INTEGER
        );

        CREATE TABLE refresh_candidate_evidence(
            candidate_id INTEGER PRIMARY KEY,
            refresh_event_result_id INTEGER NOT NULL,
            url TEXT,
            published_at TEXT,
            validation_state TEXT NOT NULL
        );

        CREATE TABLE sources(
            source_id INTEGER PRIMARY KEY,
            canonical_url_hash TEXT NOT NULL
        );

        CREATE TABLE evidence_items(
            evidence_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_id INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO assessments VALUES(1,'event_a','2026-09-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO refresh_event_results VALUES(2,'event_a',1)"
    )

    _canonical, canonical_hash, _domain = canonicalize_url(
        "https://example.com/article"
    )
    conn.execute(
        "INSERT INTO sources(source_id,canonical_url_hash) VALUES(17,?)",
        (canonical_hash,),
    )
    conn.execute(
        "INSERT INTO evidence_items VALUES(17,'event_a',17)"
    )
    conn.commit()
    return conn


def test_quarantine_rejects_same_event_canonical_official_source() -> None:
    conn = make_dedup_db()
    try:
        row = conn.execute(
            "SELECT * FROM refresh_event_results WHERE refresh_event_result_id = 2"
        ).fetchone()
        candidate = SimpleNamespace(
            ordinal=1,
            published_at="2026-09-18T12:00:00Z",
            url="https://example.com/article?utm_source=retry",
        )
        parsed = SimpleNamespace(candidates=[candidate])

        result = validate_candidates(conn, row, parsed)[1]
        assert result["validation_state"] == "rejected_source_policy"
        assert "already exists as official evidence" in (
            result["rejection_reason"] or ""
        )
    finally:
        conn.close()


def test_decision_guard_fails_closed_for_same_event_canonical_source() -> None:
    conn = make_dedup_db()
    try:
        with pytest.raises(CliError, match="already exists as official evidence"):
            assert_candidate_source_not_already_official(
                conn,
                candidate_id=25,
                event_id="event_a",
                raw_url="https://example.com/article?utm_campaign=again",
            )

        assert_candidate_source_not_already_official(
            conn,
            candidate_id=26,
            event_id="event_b",
            raw_url="https://example.com/article?utm_campaign=again",
        )
    finally:
        conn.close()
