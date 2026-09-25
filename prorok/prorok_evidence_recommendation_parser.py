#!/usr/bin/env python3
"""Strict parser/validator for one-official-evidence PROROK recommendations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from prorok_calibration import calibration_math

PARSER_VERSION = "2"
METHODOLOGY_VERSION = "refresh-calibration-v1"
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_DIRECTION = {"positive", "negative", "balanced"}
ALLOWED_IMPACT = {"none", "weak", "moderate", "strong"}
ALLOWED_INCORPORATION = {"low", "medium", "high"}
REQUIRED_KEYS = {
    "event_id", "evidence_id", "baseline_assessment_id", "baseline_probability",
    "recommended_probability", "recommended_band", "recommended_label",
    "recommendation_confidence", "change_from_baseline", "probability_delta",
    "net_evidence_direction", "net_evidence_impact", "baseline_incorporation",
    "category_transition", "recommendation_rationale", "delta_justification",
}


class EvidenceRecommendationParseError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceRecommendation:
    event_id: str
    evidence_id: int
    baseline_assessment_id: int
    baseline_probability: int
    recommended_probability: int
    recommended_band: str
    recommended_label: str
    recommendation_confidence: str
    change_from_baseline: str
    probability_delta: int
    net_evidence_direction: str
    net_evidence_impact: str
    baseline_incorporation: str
    category_transition: bool
    recommendation_rationale: str
    delta_justification: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceRecommendationParseError(f"{field} must be an integer")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceRecommendationParseError(f"{field} must be non-empty text")
    return value.strip()


def parse_evidence_recommendation(
    report: str | bytes,
    *,
    expected_event_id: str,
    expected_evidence_id: int,
    expected_baseline_assessment_id: int,
    expected_baseline_probability: int,
) -> EvidenceRecommendation:
    if isinstance(report, bytes):
        try:
            report = report.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise EvidenceRecommendationParseError("report is not valid UTF-8") from exc
    if not isinstance(report, str):
        raise EvidenceRecommendationParseError("report must be str or UTF-8 bytes")
    try:
        data = json.loads(report)
    except json.JSONDecodeError as exc:
        raise EvidenceRecommendationParseError(f"report is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceRecommendationParseError("report must be one JSON object")
    keys = set(data)
    if keys != REQUIRED_KEYS:
        missing = sorted(REQUIRED_KEYS - keys)
        extra = sorted(keys - REQUIRED_KEYS)
        raise EvidenceRecommendationParseError(f"schema mismatch: missing={missing}, extra={extra}")

    event_id = _text(data["event_id"], "event_id")
    evidence_id = _integer(data["evidence_id"], "evidence_id")
    baseline_id = _integer(data["baseline_assessment_id"], "baseline_assessment_id")
    baseline = _integer(data["baseline_probability"], "baseline_probability")
    recommended = _integer(data["recommended_probability"], "recommended_probability")
    if event_id != expected_event_id:
        raise EvidenceRecommendationParseError("event_id mismatch")
    if evidence_id != expected_evidence_id:
        raise EvidenceRecommendationParseError("evidence_id mismatch")
    if baseline_id != expected_baseline_assessment_id:
        raise EvidenceRecommendationParseError("baseline_assessment_id mismatch")
    if baseline != expected_baseline_probability:
        raise EvidenceRecommendationParseError("baseline_probability mismatch")

    try:
        math = calibration_math(baseline, recommended)
    except ValueError as exc:
        raise EvidenceRecommendationParseError(str(exc)) from exc

    # These fields are deterministic derivatives of baseline + recommendation.
    # LLM values are deliberately ignored and normalized by code so formatting
    # mistakes cannot invalidate an otherwise coherent recommendation.
    derived_fields = ("recommended_band", "recommended_label", "change_from_baseline")
    for field in derived_fields:
        if not isinstance(data[field], str):
            raise EvidenceRecommendationParseError(f"{field} must be text")
    _integer(data["probability_delta"], "probability_delta")
    if not isinstance(data["category_transition"], bool):
        raise EvidenceRecommendationParseError("category_transition must be boolean")

    confidence = _text(data["recommendation_confidence"], "recommendation_confidence").lower()
    direction = _text(data["net_evidence_direction"], "net_evidence_direction").lower()
    impact = _text(data["net_evidence_impact"], "net_evidence_impact").lower()
    incorporation = _text(data["baseline_incorporation"], "baseline_incorporation").lower()
    if confidence not in ALLOWED_CONFIDENCE:
        raise EvidenceRecommendationParseError("invalid recommendation_confidence")
    if direction not in ALLOWED_DIRECTION:
        raise EvidenceRecommendationParseError("invalid net_evidence_direction")
    if impact not in ALLOWED_IMPACT:
        raise EvidenceRecommendationParseError("invalid net_evidence_impact")
    if incorporation not in ALLOWED_INCORPORATION:
        raise EvidenceRecommendationParseError("invalid baseline_incorporation")

    return EvidenceRecommendation(
        event_id=event_id, evidence_id=evidence_id,
        baseline_assessment_id=baseline_id, baseline_probability=baseline,
        recommended_probability=recommended,
        recommended_band=math["recommended_band"],
        recommended_label=math["recommended_label"],
        recommendation_confidence=confidence,
        change_from_baseline=math["change_from_baseline"],
        probability_delta=math["probability_delta"],
        net_evidence_direction=direction, net_evidence_impact=impact,
        baseline_incorporation=incorporation,
        category_transition=math["category_transition"],
        recommendation_rationale=_text(data["recommendation_rationale"], "recommendation_rationale"),
        delta_justification=_text(data["delta_justification"], "delta_justification"),
    )
