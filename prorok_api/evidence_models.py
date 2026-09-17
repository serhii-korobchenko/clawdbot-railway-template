from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


EvidenceDirection = Literal["indicator", "counterindicator", "neutral"]
EvidenceStrength = Literal["weak", "medium", "strong"]
EventStatus = Literal["active", "paused", "resolved", "archived"]
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


class EvidenceListResponse(BaseModel):
    items: list[EvidenceListItemDTO]
    total: int
    filtered_total: int


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


class CandidateEvidenceListResponse(BaseModel):
    items: list[CandidateEvidenceDTO]
    total: int
    filtered_total: int
