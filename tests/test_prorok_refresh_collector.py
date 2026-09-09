from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from prorok.prorok_refresh_collector import collect_once


NO_EVIDENCE_REPORT = """PROROK_REFRESH_DRY_RUN
event_id: event_a
baseline_probability: 20%
search_window: 2026-09-08

CANDIDATE_EVIDENCE:
NO_NEW_EVIDENCE_FOUND
reason: Немає нових якісних evidence.

ASSESSMENT_RECOMMENDATION:
recommended_probability: n/a
recommended_band: n/a
recommended_label: n/a
confidence: medium
change_from_baseline: no_update
rationale: Підстав для зміни немає.

DB_ACTION:
do_not_write: true
next_step: очікує підтвердження користувача
"""


POSITIVE_REPORT = """PROROK_REFRESH_DRY_RUN
event_id: event_a
baseline_probability: 20%
search_window: 2026-09-08

CANDIDATE_EVIDENCE:
1.
direction: indicator
strength: medium
relevance: 90
credibility: 90
title: Test source
source: Example
url: https://example.com/a
published_at: 2026-09-08
summary: Новий факт.
why_it_matters: Змінює баланс.
duplicate_risk: low
freshness: new_after_last_assessment

ASSESSMENT_RECOMMENDATION:
recommended_probability: 30%
recommended_band: 25-35%
recommended_label: Можливо
confidence: medium
change_from_baseline: increase
rationale: Новий evidence підтримує помірне підвищення.

DB_ACTION:
do_not_write: true
next_step: очікує підтвердження користувача
"""


def make_v3_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE events(
            event_id TEXT PRIMARY KEY
        );

        CREATE TABLE assessments(
            assessment_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            assessed_at TEXT NOT NULL
        );

        CREATE TABLE runs(
            run_id INTEGER PRIMARY KEY
        );

        CREATE TABLE refresh_runs(
            refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            mode TEXT NOT NULL DEFAULT 'dry_run',
            trigger_source TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'all',
            target_event_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            events_checked INTEGER NOT NULL DEFAULT 0,
            events_with_new_evidence INTEGER NOT NULL DEFAULT 0,
            new_evidence_count INTEGER NOT NULL DEFAULT 0,
            recommendations_count INTEGER NOT NULL DEFAULT 0,
            no_change_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            model_used TEXT,
            summary TEXT,
            errors TEXT,
            phase TEXT NOT NULL DEFAULT 'scheduling',
            target_count INTEGER NOT NULL DEFAULT 0,
            scheduled_count INTEGER NOT NULL DEFAULT 0,
            scheduling_finished_at TEXT,
            collection_started_at TEXT,
            last_collected_at TEXT,
            collector_version TEXT
        );

        CREATE TABLE refresh_event_results(
            refresh_event_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_id INTEGER NOT NULL,
            event_id TEXT,
            event_title_snapshot TEXT NOT NULL,
            baseline_assessment_id INTEGER,
            baseline_probability INTEGER,
            job_state TEXT NOT NULL DEFAULT 'pending',
            outcome TEXT,
            cron_id TEXT,
            expected_run_at TEXT,
            cron_run_at_ms INTEGER,
            session_id TEXT,
            session_key TEXT,
            source_run_key TEXT UNIQUE,
            cron_summary_preview TEXT,
            transcript_raw TEXT,
            transcript_sha256 TEXT,
            parser_version TEXT,
            parse_error TEXT,
            collected_at TEXT,
            cron_status TEXT,
            duration_ms INTEGER,
            model TEXT,
            provider TEXT,
            delivered INTEGER,
            delivery_status TEXT,
            search_window TEXT,
            no_evidence_reason TEXT,
            recommended_band TEXT,
            recommended_label TEXT,
            change_from_baseline TEXT,
            do_not_write INTEGER,
            next_step TEXT,
            new_evidence_count INTEGER NOT NULL DEFAULT 0,
            indicator_count INTEGER NOT NULL DEFAULT 0,
            counterindicator_count INTEGER NOT NULL DEFAULT 0,
            recommended_probability INTEGER,
            recommendation_confidence TEXT,
            change_recommended INTEGER NOT NULL DEFAULT 0,
            recommendation_reason TEXT,
            summary TEXT,
            created_at TEXT
        );

        CREATE TABLE refresh_candidate_evidence(
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            refresh_event_result_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            direction TEXT NOT NULL,
            strength TEXT,
            relevance INTEGER,
            credibility INTEGER,
            title TEXT,
            source TEXT,
            url TEXT,
            published_at TEXT,
            summary TEXT,
            why_it_matters TEXT,
            duplicate_risk TEXT,
            freshness TEXT,
            created_at TEXT,
            UNIQUE(refresh_event_result_id, ordinal)
        );
        """
    )
    conn.execute("INSERT INTO events(event_id) VALUES('event_a')")
    conn.execute(
        """
        INSERT INTO assessments(assessment_id, event_id, assessed_at)
        VALUES(1, 'event_a', '2026-09-01T11:56:19Z')
        """
    )
    cur = conn.execute(
        """
        INSERT INTO refresh_runs(
            mode, trigger_source, scope, target_event_id, status, phase,
            target_count, scheduled_count
        )
        VALUES('dry_run','manual_cli','event','event_a','running','waiting',1,1)
        """
    )
    refresh_id = cur.lastrowid
    conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_id,event_id,event_title_snapshot,baseline_assessment_id,
            baseline_probability,job_state,cron_id
        )
        VALUES(?, 'event_a', 'Event A', 1, 20, 'scheduled', 'cron-a')
        """,
        (refresh_id,),
    )
    conn.commit()
    conn.close()


def write_run(state_dir: Path, *, status: str = "ok", summary: str = "", session_id: str | None = "session-a") -> None:
    run_dir = state_dir / "cron" / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "jobId": "cron-a",
        "action": "finished",
        "status": status,
        "runAtMs": 1788845531375,
        "durationMs": 1234,
        "model": "gpt-4.1-mini",
        "provider": "openai",
        "delivered": True,
        "deliveryStatus": "delivered",
        "sessionId": session_id,
        "sessionKey": f"agent:main:cron:cron-a:run:{session_id}" if session_id else None,
        "summary": summary,
    }
    (run_dir / "cron-a.jsonl").write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_session(
    state_dir: Path,
    text: str,
    *,
    nested: bool = False,
    filename: str = "session-a.jsonl",
) -> Path:
    session_dir = state_dir / "agents" / "main" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    if nested:
        row = {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    else:
        row = {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }
    path = session_dir / filename
    path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def get_state(db: Path) -> tuple[sqlite3.Row, sqlite3.Row, list[sqlite3.Row]]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    result = conn.execute("SELECT * FROM refresh_event_results").fetchone()
    batch = conn.execute("SELECT * FROM refresh_runs").fetchone()
    candidates = conn.execute(
        """
        SELECT *
        FROM refresh_candidate_evidence
        WHERE refresh_event_result_id = ?
        ORDER BY ordinal
        """,
        (result["refresh_event_result_id"],),
    ).fetchall()
    conn.close()
    return result, batch, candidates


def test_collect_positive_transcript(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=POSITIVE_REPORT[:100])
    write_session(state, POSITIVE_REPORT)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert result["outcome"] == "new_evidence"
    assert result["recommended_probability"] == 30
    assert result["do_not_write"] == 1
    assert result["source_run_key"]
    assert result["transcript_sha256"]
    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://example.com/a"
    assert batch["status"] == "completed"
    assert batch["phase"] == "done"
    assert batch["events_checked"] == 1
    assert batch["new_evidence_count"] == 1
    assert batch["recommendations_count"] == 1
    assert batch["error_count"] == 0


def test_collect_no_evidence_transcript(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=NO_EVIDENCE_REPORT[:100])
    write_session(state, NO_EVIDENCE_REPORT)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["outcome"] == "no_new_evidence"
    assert result["recommended_probability"] is None
    assert candidates == []
    assert batch["status"] == "completed"
    assert batch["no_change_count"] == 1


def test_deleted_session_transcript_is_used_when_live_missing(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=POSITIVE_REPORT[:100])
    write_session(
        state,
        POSITIVE_REPORT,
        filename="session-a.jsonl.deleted.2026-09-08T19-56-58.366Z",
    )

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert result["outcome"] == "new_evidence"
    assert result["recommended_probability"] == 30
    assert result["transcript_raw"] == POSITIVE_REPORT.strip()
    assert len(candidates) == 1
    assert batch["status"] == "completed"


def test_live_session_is_preferred_over_deleted_transcript(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=NO_EVIDENCE_REPORT[:100])
    write_session(state, NO_EVIDENCE_REPORT)
    write_session(
        state,
        POSITIVE_REPORT,
        filename="session-a.jsonl.deleted.2026-09-08T19-56-58.366Z",
    )

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["outcome"] == "no_new_evidence"
    assert result["recommended_probability"] is None
    assert candidates == []
    assert batch["status"] == "completed"


def test_newest_deleted_session_transcript_is_used(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=NO_EVIDENCE_REPORT[:100])
    write_session(
        state,
        NO_EVIDENCE_REPORT,
        filename="session-a.jsonl.deleted.2026-09-08T19-55-00.000Z",
    )
    write_session(
        state,
        POSITIVE_REPORT,
        filename="session-a.jsonl.deleted.2026-09-08T19-56-58.366Z",
    )

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["outcome"] == "new_evidence"
    assert result["recommended_probability"] == 30
    assert len(candidates) == 1
    assert batch["status"] == "completed"


def test_incomplete_summary_uses_deleted_full_transcript(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=POSITIVE_REPORT[:150])
    write_session(
        state,
        POSITIVE_REPORT,
        nested=True,
        filename="session-a.jsonl.deleted.2026-09-08T19-56-58.366Z",
    )

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert result["outcome"] == "new_evidence"
    assert result["recommended_probability"] == 30
    assert len(candidates) == 1
    assert batch["status"] == "completed"


def test_freshness_normalizes_old_candidate_marked_new(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = POSITIVE_REPORT.replace(
        "published_at: 2026-09-08",
        "published_at: 2026-08-30",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert result["outcome"] == "new_evidence"
    assert len(candidates) == 1
    assert candidates[0]["freshness"] == "missed_baseline_evidence"
    assert batch["status"] == "completed"


def test_freshness_accepts_old_candidate_as_missed_baseline(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = (
        POSITIVE_REPORT
        .replace("published_at: 2026-09-08", "published_at: 2026-08-30")
        .replace(
            "freshness: new_after_last_assessment",
            "freshness: missed_baseline_evidence",
        )
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert len(candidates) == 1
    assert candidates[0]["freshness"] == "missed_baseline_evidence"
    assert batch["status"] == "completed"


def test_freshness_normalizes_new_candidate_marked_missed_baseline(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = POSITIVE_REPORT.replace(
        "freshness: new_after_last_assessment",
        "freshness: missed_baseline_evidence",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert len(candidates) == 1
    assert candidates[0]["freshness"] == "new_after_last_assessment"
    assert batch["status"] == "completed"


def test_date_only_same_baseline_day_is_missed_baseline(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = POSITIVE_REPORT.replace(
        "published_at: 2026-09-08",
        "published_at: 2026-09-01",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert len(candidates) == 1
    assert candidates[0]["freshness"] == "missed_baseline_evidence"
    assert batch["status"] == "completed"


def test_invalid_published_at_still_fails_safe(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = POSITIVE_REPORT.replace(
        "published_at: 2026-09-08",
        "published_at: not-a-date",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"parse_failed": 1}
    assert result["job_state"] == "parse_failed"
    assert "must be an ISO-8601 date or datetime" in result["parse_error"]
    assert candidates == []
    assert batch["status"] == "failed"


def test_source_policy_rejects_banned_domain(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = POSITIVE_REPORT.replace(
        "https://example.com/a",
        "https://www.facebook.com/example/post/123",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"parse_failed": 1}
    assert result["job_state"] == "parse_failed"
    assert "source policy violation" in result["parse_error"]
    assert "facebook.com" in result["parse_error"]
    assert candidates == []
    assert batch["status"] == "failed"


def test_source_policy_rejects_banned_subdomain(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    report = POSITIVE_REPORT.replace(
        "https://example.com/a",
        "https://m.youtube.com/watch?v=abc",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"parse_failed": 1}
    assert "source policy violation" in result["parse_error"]
    assert "youtube.com" in result["parse_error"]
    assert candidates == []
    assert batch["status"] == "failed"


def test_url_date_consistency_rejects_conflicting_prior_date(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)

    conn = sqlite3.connect(db)
    prior_refresh_id = conn.execute(
        """
        INSERT INTO refresh_runs(
            mode, trigger_source, scope, target_event_id, status, phase,
            target_count, scheduled_count
        )
        VALUES('dry_run','manual_cli','event','event_a','completed','done',1,1)
        """
    ).lastrowid
    prior_result_id = conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_id,event_id,event_title_snapshot,baseline_assessment_id,
            baseline_probability,job_state,outcome,cron_id
        )
        VALUES(?, 'event_a', 'Event A', 1, 20, 'completed', 'new_evidence', 'prior-cron')
        """,
        (prior_refresh_id,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO refresh_candidate_evidence(
            refresh_event_result_id,ordinal,direction,strength,relevance,
            credibility,title,source,url,published_at,summary,why_it_matters,
            duplicate_risk,freshness
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            prior_result_id, 1, "indicator", "medium", 90, 90,
            "Prior", "Example", "https://example.com/a", "2026-09-05",
            "Prior summary", "Prior why", "low", "new_after_last_assessment",
        ),
    )
    conn.commit()
    conn.close()

    write_run(state)
    report = POSITIVE_REPORT.replace(
        "published_at: 2026-09-08",
        "published_at: 2026-09-09",
    )
    write_session(state, report)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"parse_failed": 1}
    assert result["job_state"] == "parse_failed"
    assert "published_at conflict" in result["parse_error"]
    assert "2026-09-05" in result["parse_error"]
    assert candidates == []
    assert batch["status"] == "failed"


def test_url_date_consistency_accepts_same_calendar_date(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)

    conn = sqlite3.connect(db)
    prior_refresh_id = conn.execute(
        """
        INSERT INTO refresh_runs(
            mode, trigger_source, scope, target_event_id, status, phase,
            target_count, scheduled_count
        )
        VALUES('dry_run','manual_cli','event','event_a','completed','done',1,1)
        """
    ).lastrowid
    prior_result_id = conn.execute(
        """
        INSERT INTO refresh_event_results(
            refresh_id,event_id,event_title_snapshot,baseline_assessment_id,
            baseline_probability,job_state,outcome,cron_id
        )
        VALUES(?, 'event_a', 'Event A', 1, 20, 'completed', 'new_evidence', 'prior-cron')
        """,
        (prior_refresh_id,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO refresh_candidate_evidence(
            refresh_event_result_id,ordinal,direction,strength,relevance,
            credibility,title,source,url,published_at,summary,why_it_matters,
            duplicate_risk,freshness
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            prior_result_id, 1, "indicator", "medium", 90, 90,
            "Prior", "Example", "https://example.com/a", "2026-09-08T06:00:00Z",
            "Prior summary", "Prior why", "low", "new_after_last_assessment",
        ),
    )
    conn.commit()
    conn.close()

    write_run(state)
    write_session(state, POSITIVE_REPORT)

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert len(candidates) == 1
    assert candidates[0]["published_at"] == "2026-09-08"
    assert batch["status"] == "completed"


def test_complete_summary_fallback_when_session_missing(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=NO_EVIDENCE_REPORT)

    counts = collect_once(db, state)
    result, batch, _ = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
    assert result["outcome"] == "no_new_evidence"
    assert batch["status"] == "completed"


def test_incomplete_summary_is_not_used_as_fallback(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, summary=NO_EVIDENCE_REPORT[:150])

    counts = collect_once(db, state)
    result, batch, _ = get_state(db)

    assert counts == {"source_missing": 1}
    assert result["job_state"] == "source_missing"
    assert result["outcome"] == "error"
    assert batch["status"] == "failed"
    assert batch["error_count"] == 1


def test_parse_failure_is_terminal_and_audited(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    write_session(state, POSITIVE_REPORT.replace("do_not_write: true", "do_not_write: false"))

    counts = collect_once(db, state)
    result, batch, candidates = get_state(db)

    assert counts == {"parse_failed": 1}
    assert result["job_state"] == "parse_failed"
    assert result["outcome"] == "error"
    assert "do_not_write must be true" in result["parse_error"]
    assert result["transcript_raw"]
    assert result["transcript_sha256"]
    assert candidates == []
    assert batch["status"] == "failed"


def test_execution_failure_is_terminal(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state, status="error")

    counts = collect_once(db, state)
    result, batch, _ = get_state(db)

    assert counts == {"execution_failed": 1}
    assert result["job_state"] == "execution_failed"
    assert result["outcome"] == "error"
    assert batch["status"] == "failed"


def test_second_pass_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    write_session(state, POSITIVE_REPORT)

    first = collect_once(db, state)
    result1, batch1, candidates1 = get_state(db)
    second = collect_once(db, state)
    result2, batch2, candidates2 = get_state(db)

    assert first == {"completed": 1}
    assert second == {}
    assert result2["source_run_key"] == result1["source_run_key"]
    assert result2["transcript_sha256"] == result1["transcript_sha256"]
    assert len(candidates1) == len(candidates2) == 1
    assert batch2["new_evidence_count"] == batch1["new_evidence_count"] == 1


def test_nested_openclaw_message_shape_is_supported(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    state = tmp_path / "state"
    make_v3_db(db)
    write_run(state)
    write_session(state, NO_EVIDENCE_REPORT, nested=True)

    counts = collect_once(db, state)
    result, _, _ = get_state(db)

    assert counts == {"completed": 1}
    assert result["job_state"] == "completed"
