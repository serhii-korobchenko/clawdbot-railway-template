from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .models import Confidence, RefreshDecisionDTO


class LatestRefreshRunDTO(BaseModel):
    refresh_id: int
    mode: str
    trigger_source: str
    scope: str
    target_event_id: str | None
    started_at: str
    finished_at: str | None
    status: str
    phase: str
    events_checked: int
    events_with_new_evidence: int
    new_evidence_count: int
    recommendations_count: int
    no_change_count: int
    error_count: int
    collector_version: str | None


class LatestRefreshEventResultDTO(BaseModel):
    refresh_event_result_id: int
    event_id: str | None
    event_title_snapshot: str
    job_state: str
    outcome: str | None
    baseline_probability: int | None
    new_evidence_count: int
    indicator_count: int
    counterindicator_count: int
    candidate_rejected_count: int
    recommendation_valid: bool
    recommended_probability: int | None
    recommendation_confidence: Confidence | None
    change_recommended: bool
    current_probability: int | None
    decision: RefreshDecisionDTO | None


class LatestRefreshResponse(BaseModel):
    refresh: LatestRefreshRunDTO | None
    results: list[LatestRefreshEventResultDTO]
