from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


EventStatus = Literal["active", "paused", "resolved", "archived"]
Confidence = Literal["low", "medium", "high"]
EvidenceDirection = Literal["indicator", "counterindicator", "neutral"]
EvidenceStrength = Literal["weak", "medium", "strong"]
RefreshDecisionType = Literal[
    "accept_recommendation",
    "custom_probability",
    "keep_current",
]
RecommendationStatus = Literal["actionable", "decided", "stale", "no_change"]


class DecisionCriteriaDTO(BaseModel):
    format: Literal["structured", "text"]
    data: dict[str, Any] | None
    raw: str


class CurrentAssessmentDTO(BaseModel):
    assessment_id: int
    assessed_at: str
    probability_percent: int
    probability_band: str
    probability_label: str
    confidence: Confidence | None
    delta_from_previous: int | None


class AssessmentDTO(CurrentAssessmentDTO):
    run_id: int | None
    rationale: str | None


class SourceDTO(BaseModel):
    source_id: int
    title: str | None
    domain: str | None
    url: str
    canonical_url: str
    published_at: str | None
    source_type: str | None


class EvidenceDTO(BaseModel):
    evidence_id: int
    run_id: int | None
    created_at: str
    direction: EvidenceDirection
    strength: EvidenceStrength | None
    summary: str
    relevance: int | None
    credibility: int | None
    source: SourceDTO


class EventSummaryDTO(BaseModel):
    event_id: str
    title: str
    question: str
    status: EventStatus
    forecast_horizon: str | None
    current_assessment: CurrentAssessmentDTO | None
    assessment_count: int
    evidence_count: int
    source_count: int
    created_at: str
    updated_at: str
    archived_at: str | None


class EventListResponse(BaseModel):
    items: list[EventSummaryDTO]
    total: int
    filtered_total: int
    status_counts: dict[EventStatus, int]


class EventProvenanceDTO(BaseModel):
    source_image_note: str | None


class EventDTO(BaseModel):
    event_id: str
    title: str
    question: str
    status: EventStatus
    forecast_horizon: str | None
    tags: list[str]
    decision_criteria: DecisionCriteriaDTO
    provenance: EventProvenanceDTO
    created_at: str
    updated_at: str
    archived_at: str | None


class LimitationsDTO(BaseModel):
    assessment_evidence_attribution: Literal["unavailable"] = "unavailable"


class EventDetailResponse(BaseModel):
    event: EventDTO
    current_assessment: CurrentAssessmentDTO | None
    assessments: list[AssessmentDTO]
    evidence: list[EvidenceDTO]
    limitations: LimitationsDTO


class RefreshDecisionDTO(BaseModel):
    decision_id: int
    decision_type: RefreshDecisionType
    selected_probability: int
    assessment_id: int | None
    decision_source: str
    decided_at: str


class LatestRecommendationDTO(BaseModel):
    refresh_event_result_id: int
    refresh_id: int
    created_at: str
    outcome: str | None
    baseline_assessment_id: int
    baseline_probability: int
    recommended_probability: int
    recommended_band: str | None
    recommended_label: str | None
    recommendation_confidence: Confidence | None
    recommendation_reason: str | None
    change_recommended: bool
    candidate_rejected_count: int
    recommendation_valid: bool
    current_assessment_id: int | None
    current_probability: int | None
    is_stale: bool
    actionable: bool
    status: RecommendationStatus
    decision: RefreshDecisionDTO | None


class LatestRecommendationResponse(BaseModel):
    event_id: str
    recommendation: LatestRecommendationDTO | None
