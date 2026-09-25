from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EvidenceDirection = Literal["indicator", "counterindicator", "neutral"]
EvidenceStrength = Literal["weak", "medium", "strong"]
EventStatus = Literal["active", "paused", "resolved", "archived"]
EvidenceActivityWindow = Literal["7d", "30d", "all"]
CandidateValidationState = Literal[
    "legacy_unvalidated",
    "accepted",
    "rejected_source_policy",
    "rejected_date_conflict",
    "rejected_invalid_metadata",
]


class EvidenceEventDTO(BaseModel):
    event_id: str
    title: str
    status: EventStatus


class EvidenceSourceDTO(BaseModel):
    source_id: int
    title: str | None
    domain: str | None
    url: str
    canonical_url: str
    published_at: str | None
    source_type: str | None


class EvidenceAssessmentDTO(BaseModel):
    status: Literal["assessed_changed", "assessed_unchanged", "unknown"]
    refresh_id: int | None = None
    refresh_event_result_id: int | None = None
    decision_id: int | None = None
    decision_type: Literal["accept_recommendation", "custom_probability", "keep_current", "evidence_manual"] | None = None
    provenance_type: Literal["refresh", "evidence_manual"] | None = None
    evidence_assessment_decision_id: int | None = None
    baseline_probability: int | None = None
    selected_probability: int | None = None
    assessment_id: int | None = None
    decided_at: str | None = None


class EvidenceProrokAppDTO(BaseModel):
    state: Literal["marked", "unmarked"] = "unmarked"
    source: str | None = None
    actor: str | None = None
    changed_at: str | None = None


class EvidenceListItemDTO(BaseModel):
    evidence_id: int
    run_id: int | None
    created_at: str
    direction: EvidenceDirection
    strength: EvidenceStrength | None
    summary: str
    relevance: int | None
    credibility: int | None
    event: EvidenceEventDTO
    source: EvidenceSourceDTO
    assessment: EvidenceAssessmentDTO
    prorok_app: EvidenceProrokAppDTO = Field(default_factory=EvidenceProrokAppDTO)


class EvidenceListResponse(BaseModel):
    items: list[EvidenceListItemDTO]
    total: int
    filtered_total: int


class EvidenceActivityItemDTO(BaseModel):
    event_id: str
    title: str
    status: EventStatus
    evidence_count: int
    indicator_count: int
    counterindicator_count: int
    neutral_count: int
    latest_evidence_at: str | None


class EvidenceActivityResponse(BaseModel):
    status: EventStatus
    window: EvidenceActivityWindow
    generated_at: str
    cutoff_at: str | None
    total_events: int
    total_evidence: int
    items: list[EvidenceActivityItemDTO]


class CandidateEvidenceDTO(BaseModel):
    candidate_id: int
    refresh_event_result_id: int
    refresh_id: int
    event_id: str | None
    event_title: str
    ordinal: int
    direction: Literal["indicator", "counterindicator"]
    strength: EvidenceStrength | None
    relevance: int | None
    credibility: int | None
    title: str | None
    source: str | None
    url: str | None
    published_at: str | None
    summary: str | None
    why_it_matters: str | None
    duplicate_risk: str | None
    freshness: str | None
    validation_state: CandidateValidationState
    rejection_reason: str | None
    created_at: str
    decision_id: int | None
    decision_type: Literal["accept_recommendation", "custom_probability", "keep_current"] | None
    selected_probability: int | None
    decided_at: str | None
    candidate_review_decision_id: int | None
    review_decision_type: Literal["accept", "reject"] | None
    review_decided_at: str | None
    promotion_action: Literal["inserted", "reused"] | None
    evidence_id: int | None
    promoted_at: str | None


class CandidateEvidenceListResponse(BaseModel):
    items: list[CandidateEvidenceDTO]
    total: int
    filtered_total: int


class CandidateAssessmentRecommendationResponse(BaseModel):
    candidate_assessment_recommendation_id: int
    candidate_id: int
    event_id_snapshot: str
    baseline_assessment_id: int
    baseline_probability: int
    recommended_probability: int
    probability_delta: int
    recommended_band: str
    recommended_label: str
    recommendation_confidence: Literal["low", "medium", "high"]
    change_from_baseline: Literal["increase", "decrease", "no_update"]
    net_evidence_direction: Literal["positive", "negative", "balanced"]
    net_evidence_impact: Literal["none", "weak", "moderate", "strong"]
    baseline_incorporation: Literal["low", "medium", "high"]
    category_transition: bool
    recommendation_rationale: str
    delta_justification: str
    methodology_version: str
    parser_version: str
    agent_id: str | None
    model_used: str | None
    run_id: int | None
    source_run_key: str | None
    created_at: str
    status: Literal["ready", "stale", "error"]
