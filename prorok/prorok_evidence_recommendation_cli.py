#!/usr/bin/env python3
"""Prepare and persist a PROROK recommendation for one official evidence item.

Safety boundary: this module may insert evidence_assessment_recommendations only.
It never writes events, evidence_items, assessments, or evidence_assessment_decisions.
"""
from __future__ import annotations

import argparse
import os
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from .prorok_evidence_recommendation_parser import (
        METHODOLOGY_VERSION,
        PARSER_VERSION,
        EvidenceRecommendationParseError,
        parse_evidence_recommendation,
    )
except ImportError:
    from prorok_evidence_recommendation_parser import (
        METHODOLOGY_VERSION,
        PARSER_VERSION,
        EvidenceRecommendationParseError,
        parse_evidence_recommendation,
    )

DEFAULT_DB = "/data/workspace/prorok/prorok.sqlite3"
MIN_SCHEMA_VERSION = 13
DEFAULT_AGENT_ID = "prorok-refresh"
DEFAULT_AGENT_TIMEOUT_SECONDS = 180


class CliError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecommendationContext:
    event_id: str
    event_title: str
    question: str
    forecast_horizon: str
    decision_criteria: str
    evidence_id: int
    evidence_direction: str
    evidence_strength: str
    evidence_relevance: str
    evidence_credibility: str
    evidence_summary: str
    baseline_assessment_id: int
    baseline_probability: int
    baseline_confidence: str
    baseline_rationale: str


def resolve_db(explicit: str | None) -> Path:
    return Path(explicit or os.getenv("PROROK_DB_PATH") or os.getenv("PROROK_DB") or DEFAULT_DB).expanduser().resolve()


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise CliError(f"PROROK DB not found: {path}")
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def require_schema_v13(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    try:
        version = int(row["value"]) if row else None
    except (TypeError, ValueError) as exc:
        raise CliError(f"schema v13+ required; current schema_version={row['value'] if row else None!r}") from exc
    if version is None or version < MIN_SCHEMA_VERSION:
        raise CliError(f"schema v13+ required; current schema_version={version!r}")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_assessment_recommendations'").fetchone() is None:
        raise CliError("schema v13 table evidence_assessment_recommendations is missing")


def load_context(conn: sqlite3.Connection, event_id: str, evidence_id: int) -> RecommendationContext:
    event = conn.execute(
        """SELECT event_id,title,question,forecast_horizon,decision_criteria
           FROM events WHERE event_id=?""", (event_id,)
    ).fetchone()
    if event is None:
        raise CliError(f"event not found: {event_id}")
    evidence = conn.execute(
        """SELECT evidence_id,event_id,direction,strength,relevance,credibility,summary
           FROM evidence_items WHERE evidence_id=?""", (evidence_id,)
    ).fetchone()
    if evidence is None:
        raise CliError(f"evidence not found: {evidence_id}")
    if str(evidence["event_id"]) != event_id:
        raise CliError("evidence does not belong to the requested event")
    baseline = conn.execute(
        """SELECT assessment_id,probability_percent,confidence,rationale
           FROM assessments WHERE event_id=?
           ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1""", (event_id,)
    ).fetchone()
    if baseline is None:
        raise CliError("event has no current assessment")
    return RecommendationContext(
        event_id=event_id,
        event_title=str(event["title"] or ""),
        question=str(event["question"] or ""),
        forecast_horizon=str(event["forecast_horizon"] or ""),
        decision_criteria=str(event["decision_criteria"] or ""),
        evidence_id=int(evidence["evidence_id"]),
        evidence_direction=str(evidence["direction"] or "neutral"),
        evidence_strength=str(evidence["strength"] or "n/a"),
        evidence_relevance=str(evidence["relevance"] if evidence["relevance"] is not None else "n/a"),
        evidence_credibility=str(evidence["credibility"] if evidence["credibility"] is not None else "n/a"),
        evidence_summary=str(evidence["summary"] or ""),
        baseline_assessment_id=int(baseline["assessment_id"]),
        baseline_probability=int(baseline["probability_percent"]),
        baseline_confidence=str(baseline["confidence"] or "medium"),
        baseline_rationale=str(baseline["rationale"] or ""),
    )


def build_prompt(ctx: RecommendationContext) -> str:
    return f"""You are the PROROK forecasting calibration agent.
Analyze ONLY the supplied official evidence in the context of the event and current baseline.
Do not search the web. Do not create evidence. Do not write or change any official assessment.

EVENT
event_id: {ctx.event_id}
title: {ctx.event_title}
question: {ctx.question}
forecast_horizon: {ctx.forecast_horizon}
decision_criteria: {ctx.decision_criteria}

CURRENT BASELINE
baseline_assessment_id: {ctx.baseline_assessment_id}
baseline_probability: {ctx.baseline_probability}
confidence: {ctx.baseline_confidence}
rationale: {ctx.baseline_rationale}

OFFICIAL EVIDENCE
evidence_id: {ctx.evidence_id}
direction: {ctx.evidence_direction}
strength: {ctx.evidence_strength}
relevance: {ctx.evidence_relevance}
credibility: {ctx.evidence_credibility}
summary: {ctx.evidence_summary}

CALIBRATION RULES
- Judge novelty/independence, credibility, relevance, directness to resolution criteria, forecast horizon,
  counterevidence, magnitude, and whether the information is already incorporated in the baseline.
- Do not mechanically count indicators/counterindicators.
- Already-priced confirmation should not automatically move probability.
- A qualitative-band transition requires stronger justification than movement within a band.
- recommended_probability must be one of 0,5,10,...,100.
- Scale: 0-5%=Віддалена можливість; 10-20%=Ймовірність низька; 25-35%=Малоймовірно;
  40-50%=Реалістична можливість; 55-75%=Ймовірно; 80-90%=Висока ймовірність;
  95-100%=Майже напевно.
- probability_delta = recommended_probability - baseline_probability.
- category_transition is true only when the qualitative band changes.
- A no-change recommendation is valid.
- Always write all user-facing explanatory text in Ukrainian, regardless of the language of the evidence.
- In particular, recommendation_rationale and delta_justification MUST be in Ukrainian.

Return ONLY one JSON object with exactly these keys:
event_id, evidence_id, baseline_assessment_id, baseline_probability,
recommended_probability, recommended_band, recommended_label,
recommendation_confidence, change_from_baseline, probability_delta,
net_evidence_direction, net_evidence_impact, baseline_incorporation,
category_transition, recommendation_rationale, delta_justification.

Allowed enums:
recommendation_confidence: low|medium|high
change_from_baseline: increase|decrease|no_update
net_evidence_direction: positive|negative|balanced
net_evidence_impact: none|weak|moderate|strong
baseline_incorporation: low|medium|high
category_transition: true|false
"""



def extract_agent_report(stdout: str) -> tuple[str, str | None]:
    """Extract the strict recommendation JSON from OpenClaw --json output."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CliError(f"OpenClaw agent returned invalid JSON envelope: {exc}") from exc

    candidates: list[str] = []
    model_used: str | None = None
    if isinstance(envelope, dict):
        for key in ("model", "model_used"):
            value = envelope.get(key)
            if isinstance(value, str) and value.strip():
                model_used = value.strip()
                break

        for key in ("response", "output", "text", "message", "content"):
            value = envelope.get(key)
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, dict):
                for nested in ("text", "content", "response", "output"):
                    nested_value = value.get(nested)
                    if isinstance(nested_value, str):
                        candidates.append(nested_value)

        result = envelope.get("result")
        if isinstance(result, dict):
            for key in ("response", "output", "text", "message", "content"):
                value = result.get(key)
                if isinstance(value, str):
                    candidates.append(value)
                elif isinstance(value, dict):
                    nested_value = value.get("text") or value.get("content")
                    if isinstance(nested_value, str):
                        candidates.append(nested_value)

            # OpenClaw 2026.5.22 Gateway JSON envelope:
            # result.payloads[].text contains the agent reply.
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                for payload in payloads:
                    if not isinstance(payload, dict):
                        continue
                    value = payload.get("text")
                    if isinstance(value, str):
                        candidates.append(value)

            # Agent metadata may contain the effective model. Walk only
            # dictionaries/lists and take the first non-empty "model" value.
            if not model_used:
                stack = [result]
                while stack and not model_used:
                    node = stack.pop()
                    if isinstance(node, dict):
                        value = node.get("model")
                        if isinstance(value, str) and value.strip():
                            model_used = value.strip()
                            break
                        stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
                    elif isinstance(node, list):
                        stack.extend(v for v in node if isinstance(v, (dict, list)))

    for candidate in candidates:
        candidate = candidate.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate, model_used

    raise CliError("OpenClaw agent JSON envelope does not contain a strict JSON recommendation report")


def run_openclaw_agent(
    prompt: str,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
    runner=subprocess.run,
) -> tuple[str, str | None]:
    """Run one isolated PROROK agent turn and return the embedded strict report."""
    cmd = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--message",
        prompt,
        "--json",
    ]
    try:
        completed = runner(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"OpenClaw agent timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise CliError(f"failed to start OpenClaw agent: {exc}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        detail = stderr[-800:] if stderr else "no stderr"
        raise CliError(f"OpenClaw agent failed with exit code {completed.returncode}: {detail}")

    return extract_agent_report(completed.stdout)

def ensure_baseline_current(conn: sqlite3.Connection, ctx: RecommendationContext) -> None:
    row = conn.execute(
        """SELECT assessment_id,probability_percent FROM assessments WHERE event_id=?
           ORDER BY assessed_at DESC,assessment_id DESC LIMIT 1""", (ctx.event_id,)
    ).fetchone()
    if row is None:
        raise CliError("event has no current assessment")
    if int(row["assessment_id"]) != ctx.baseline_assessment_id or int(row["probability_percent"]) != ctx.baseline_probability:
        raise CliError(
            f"stale baseline: expected assessment_id={ctx.baseline_assessment_id} probability={ctx.baseline_probability}%, "
            f"current assessment_id={row['assessment_id']} probability={row['probability_percent']}%"
        )


def persist_report(
    conn: sqlite3.Connection,
    ctx: RecommendationContext,
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
            expected_evidence_id=ctx.evidence_id,
            expected_baseline_assessment_id=ctx.baseline_assessment_id,
            expected_baseline_probability=ctx.baseline_probability,
        )
    except EvidenceRecommendationParseError as exc:
        raise CliError(str(exc)) from exc

    conn.execute("BEGIN IMMEDIATE")
    try:
        ensure_baseline_current(conn, ctx)
        recommendation_id = conn.execute(
            """INSERT INTO evidence_assessment_recommendations(
                event_id_snapshot,evidence_id_snapshot,evidence_id,baseline_assessment_id,baseline_probability,
                recommended_probability,probability_delta,recommended_band,recommended_label,
                recommendation_confidence,change_from_baseline,net_evidence_direction,
                net_evidence_impact,baseline_incorporation,category_transition,
                recommendation_rationale,delta_justification,methodology_version,parser_version,
                agent_id,model_used,run_id,source_run_key,status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ready')""",
            (
                ctx.event_id,ctx.evidence_id,ctx.evidence_id,ctx.baseline_assessment_id,ctx.baseline_probability,
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
    p=argparse.ArgumentParser(description="Prepare/persist one-official-evidence PROROK recommendation")
    p.add_argument("event_id"); p.add_argument("evidence_id",type=int); p.add_argument("--db")
    p.add_argument("--report-file",help="Strict JSON agent report to validate and persist instead of invoking OpenClaw")
    p.add_argument("--print-prompt",action="store_true",help="Print prompt only; do not invoke agent or persist")
    p.add_argument("--dry-run-json",action="store_true",help="Invoke agent and print its raw strict JSON report; do not validate or persist")
    p.add_argument("--agent-id",default=DEFAULT_AGENT_ID)
    p.add_argument("--agent-timeout-seconds",type=int,default=DEFAULT_AGENT_TIMEOUT_SECONDS)
    p.add_argument("--model-used"); p.add_argument("--run-id",type=int); p.add_argument("--source-run-key")
    p.add_argument("--output-json",action="store_true",help="Emit the persisted recommendation as one JSON object")
    a=p.parse_args(argv)
    try:
        conn=connect(resolve_db(a.db))
        try:
            require_schema_v13(conn)
            ctx=load_context(conn,a.event_id,a.evidence_id)
            prompt=build_prompt(ctx)
            if a.print_prompt:
                print(prompt)
                return 0
            model_used=a.model_used
            if a.report_file:
                report=Path(a.report_file).read_text(encoding="utf-8")
            else:
                report, detected_model=run_openclaw_agent(
                    prompt,
                    agent_id=a.agent_id,
                    timeout_seconds=a.agent_timeout_seconds,
                )
                if not model_used:
                    model_used=detected_model
            if a.dry_run_json:
                print(report)
                return 0
            rid=persist_report(conn,ctx,report,agent_id=a.agent_id,model_used=model_used,run_id=a.run_id,source_run_key=a.source_run_key)
            if a.output_json:
                row=conn.execute("SELECT * FROM evidence_assessment_recommendations WHERE evidence_assessment_recommendation_id=?",(rid,)).fetchone()
                print(json.dumps(dict(row),ensure_ascii=False,separators=(",",":")))
            else:
                print("OK: evidence recommendation persisted")
                print(f"evidence_assessment_recommendation_id: {rid}")
                print(f"event_id: {ctx.event_id}")
                print(f"evidence_id: {ctx.evidence_id}")
                print(f"baseline_assessment_id: {ctx.baseline_assessment_id}")
            return 0
        finally:
            conn.close()
    except Exception as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
