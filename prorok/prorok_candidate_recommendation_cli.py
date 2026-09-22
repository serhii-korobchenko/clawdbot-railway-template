#!/usr/bin/env python3
"""Prepare and persist a PROROK preliminary recommendation for one Candidate Evidence item.

Safety boundary: this module writes candidate_assessment_recommendations only.
It never promotes a Candidate, creates official evidence, or changes assessments.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

try:
    from . import prorok_evidence_recommendation_cli as official
    from .prorok_evidence_recommendation_parser import (
        METHODOLOGY_VERSION,
        PARSER_VERSION,
        EvidenceRecommendationParseError,
        parse_evidence_recommendation,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import prorok_evidence_recommendation_cli as official
    from prorok_evidence_recommendation_parser import (
        METHODOLOGY_VERSION,
        PARSER_VERSION,
        EvidenceRecommendationParseError,
        parse_evidence_recommendation,
    )

CliError = official.CliError
DEFAULT_AGENT_ID = official.DEFAULT_AGENT_ID
DEFAULT_AGENT_TIMEOUT_SECONDS = official.DEFAULT_AGENT_TIMEOUT_SECONDS
MIN_SCHEMA_VERSION = 16


def require_schema_v16(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    try:
        version = int(row["value"]) if row else None
    except (TypeError, ValueError) as exc:
        raise CliError(f"schema v16+ required; current schema_version={row['value'] if row else None!r}") from exc
    if version is None or version < MIN_SCHEMA_VERSION:
        raise CliError(f"schema v16+ required; current schema_version={version!r}")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_assessment_recommendations'").fetchone() is None:
        raise CliError("schema v16 table candidate_assessment_recommendations is missing")


def load_context(conn: sqlite3.Connection, candidate_id: int) -> official.RecommendationContext:
    row = conn.execute(
        """SELECT ce.candidate_id,ce.direction,ce.strength,ce.relevance,ce.credibility,ce.summary,
                  ce.validation_state,rr.event_id
           FROM refresh_candidate_evidence ce
           JOIN refresh_event_results rr ON rr.refresh_event_result_id=ce.refresh_event_result_id
           WHERE ce.candidate_id=?""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise CliError(f"candidate_id not found: {candidate_id}")
    if row["event_id"] is None:
        raise CliError("candidate is detached from an event")
    if str(row["validation_state"] or "") != "accepted":
        raise CliError(f"candidate is not quarantine-accepted: validation_state={row['validation_state']}")

    event_id = str(row["event_id"])
    event = conn.execute(
        "SELECT event_id,title,question,forecast_horizon,decision_criteria FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if event is None:
        raise CliError(f"event not found: {event_id}")
    baseline = conn.execute(
        """SELECT assessment_id,probability_percent,confidence,rationale
           FROM assessments WHERE event_id=?
           ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1""",
        (event_id,),
    ).fetchone()
    if baseline is None:
        raise CliError("event has no current assessment")

    return official.RecommendationContext(
        event_id=event_id,
        event_title=str(event["title"] or ""),
        question=str(event["question"] or ""),
        forecast_horizon=str(event["forecast_horizon"] or ""),
        decision_criteria=str(event["decision_criteria"] or ""),
        evidence_id=int(candidate_id),
        evidence_direction=str(row["direction"] or "neutral"),
        evidence_strength=str(row["strength"] or "n/a"),
        evidence_relevance=str(row["relevance"] if row["relevance"] is not None else "n/a"),
        evidence_credibility=str(row["credibility"] if row["credibility"] is not None else "n/a"),
        evidence_summary=str(row["summary"] or ""),
        baseline_assessment_id=int(baseline["assessment_id"]),
        baseline_probability=int(baseline["probability_percent"]),
        baseline_confidence=str(baseline["confidence"] or "medium"),
        baseline_rationale=str(baseline["rationale"] or ""),
    )


def build_prompt(ctx: official.RecommendationContext, candidate_id: int) -> str:
    prompt = official.build_prompt(ctx)
    prompt = prompt.replace(
        "Analyze ONLY the supplied official evidence in the context of the event and current baseline.",
        "Analyze ONLY the supplied Candidate Evidence in the context of the event and current baseline. The Candidate is quarantined and is NOT official evidence.",
    ).replace("OFFICIAL EVIDENCE", "CANDIDATE EVIDENCE")
    prompt = prompt.replace(f"evidence_id: {candidate_id}", f"candidate_id: {candidate_id}")
    prompt = prompt.replace("event_id, evidence_id, baseline_assessment_id, baseline_probability,", "event_id, evidence_id, baseline_assessment_id, baseline_probability,")
    prompt += "\nFor parser compatibility, return evidence_id equal to candidate_id. This does not make the Candidate official evidence.\n"
    return prompt


def persist_report(
    conn: sqlite3.Connection,
    ctx: official.RecommendationContext,
    candidate_id: int,
    report: str,
    *,
    agent_id: str | None = None,
    model_used: str | None = None,
    run_id: int | None = None,
    source_run_key: str | None = None,
) -> int:
    try:
        parsed = parse_evidence_recommendation(
            report,
            expected_event_id=ctx.event_id,
            expected_evidence_id=candidate_id,
            expected_baseline_assessment_id=ctx.baseline_assessment_id,
            expected_baseline_probability=ctx.baseline_probability,
        )
    except EvidenceRecommendationParseError as exc:
        raise CliError(str(exc)) from exc

    conn.execute("BEGIN IMMEDIATE")
    try:
        official.ensure_baseline_current(conn, ctx)
        recommendation_id = conn.execute(
            """INSERT INTO candidate_assessment_recommendations(
                candidate_id,event_id_snapshot,baseline_assessment_id,baseline_probability,
                recommended_probability,probability_delta,recommended_band,recommended_label,
                recommendation_confidence,change_from_baseline,net_evidence_direction,
                net_evidence_impact,baseline_incorporation,category_transition,
                recommendation_rationale,delta_justification,methodology_version,parser_version,
                agent_id,model_used,run_id,source_run_key,status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ready')""",
            (
                candidate_id,ctx.event_id,ctx.baseline_assessment_id,ctx.baseline_probability,
                parsed.recommended_probability,parsed.probability_delta,parsed.recommended_band,
                parsed.recommended_label,parsed.recommendation_confidence,parsed.change_from_baseline,
                parsed.net_evidence_direction,parsed.net_evidence_impact,parsed.baseline_incorporation,
                int(parsed.category_transition),parsed.recommendation_rationale,parsed.delta_justification,
                METHODOLOGY_VERSION,PARSER_VERSION,agent_id,model_used,run_id,source_run_key,
            ),
        ).lastrowid
        conn.commit()
        return int(recommendation_id)
    except Exception:
        conn.rollback()
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prepare/persist one-Candidate PROROK preliminary recommendation")
    p.add_argument("candidate_id", type=int); p.add_argument("--db")
    p.add_argument("--report-file")
    p.add_argument("--print-prompt", action="store_true")
    p.add_argument("--dry-run-json", action="store_true")
    p.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    p.add_argument("--agent-timeout-seconds", type=int, default=DEFAULT_AGENT_TIMEOUT_SECONDS)
    p.add_argument("--model-used"); p.add_argument("--run-id", type=int); p.add_argument("--source-run-key")
    try:
        conn = official.connect(official.resolve_db(p.parse_args(argv).db))
        a = p.parse_args(argv)
        try:
            require_schema_v16(conn)
            ctx = load_context(conn, a.candidate_id)
            prompt = build_prompt(ctx, a.candidate_id)
            if a.print_prompt:
                print(prompt); return 0
            model_used = a.model_used
            if a.report_file:
                report = Path(a.report_file).read_text(encoding="utf-8")
            else:
                report, detected_model = official.run_openclaw_agent(prompt, agent_id=a.agent_id, timeout_seconds=a.agent_timeout_seconds)
                model_used = model_used or detected_model
            if a.dry_run_json:
                print(report); return 0
            rid = persist_report(conn, ctx, a.candidate_id, report, agent_id=a.agent_id, model_used=model_used, run_id=a.run_id, source_run_key=a.source_run_key)
            print(f"candidate_assessment_recommendation_id: {rid}")
            return 0
        finally:
            conn.close()
    except (CliError, sqlite3.Error, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
