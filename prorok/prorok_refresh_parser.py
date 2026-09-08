#!/usr/bin/env python3
"""Strict deterministic parser for PROROK_REFRESH_DRY_RUN reports.

The parser is intentionally conservative. It validates the report contract and
returns structured data for refresh audit/candidate tables only. It never writes
SQLite and never creates official PROROK evidence or assessments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PARSER_VERSION = "1"

HEADER = "PROROK_REFRESH_DRY_RUN"
CANDIDATE_SECTION = "CANDIDATE_EVIDENCE:"
ASSESSMENT_SECTION = "ASSESSMENT_RECOMMENDATION:"
DB_ACTION_SECTION = "DB_ACTION:"
NO_EVIDENCE = "NO_NEW_EVIDENCE_FOUND"

ALLOWED_DIRECTIONS = {"indicator", "counterindicator"}
ALLOWED_STRENGTHS = {"weak", "medium", "strong"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_DUPLICATE_RISK = {"low", "medium", "high"}
ALLOWED_FRESHNESS = {"new_after_last_assessment", "missed_baseline_evidence"}
ALLOWED_BANDS = {
    "0-5%",
    "10-20%",
    "25-35%",
    "40-50%",
    "55-75%",
    "80-90%",
    "95-100%",
}

HEADER_KEYS = ("event_id", "baseline_probability", "search_window")
CANDIDATE_KEYS = (
    "direction",
    "strength",
    "relevance",
    "credibility",
    "title",
    "source",
    "url",
    "published_at",
    "summary",
    "why_it_matters",
    "duplicate_risk",
    "freshness",
)
ASSESSMENT_KEYS = (
    "recommended_probability",
    "recommended_band",
    "recommended_label",
    "confidence",
    "change_from_baseline",
    "rationale",
)
DB_ACTION_KEYS = ("do_not_write", "next_step")


class RefreshParseError(ValueError):
    """Raised when a refresh report does not satisfy the strict contract."""


@dataclass(frozen=True)
class RefreshCandidate:
    ordinal: int
    direction: str
    strength: str
    relevance: int
    credibility: int
    title: str
    source: str
    url: str
    published_at: str
    summary: str
    why_it_matters: str
    duplicate_risk: str
    freshness: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "direction": self.direction,
            "strength": self.strength,
            "relevance": self.relevance,
            "credibility": self.credibility,
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at,
            "summary": self.summary,
            "why_it_matters": self.why_it_matters,
            "duplicate_risk": self.duplicate_risk,
            "freshness": self.freshness,
        }


@dataclass(frozen=True)
class RefreshParseResult:
    event_id: str
    baseline_probability: int
    search_window: str
    outcome: str
    no_evidence_reason: str | None
    candidates: tuple[RefreshCandidate, ...]
    recommended_probability: int | None
    recommended_band: str | None
    recommended_label: str | None
    recommendation_confidence: str
    change_from_baseline: str
    recommendation_reason: str
    do_not_write: bool
    next_step: str

    @property
    def new_evidence_count(self) -> int:
        return len(self.candidates)

    @property
    def indicator_count(self) -> int:
        return sum(1 for item in self.candidates if item.direction == "indicator")

    @property
    def counterindicator_count(self) -> int:
        return sum(
            1 for item in self.candidates if item.direction == "counterindicator"
        )

    @property
    def change_recommended(self) -> bool:
        return self.change_from_baseline in {"increase", "decrease"}

    def event_result_fields(self) -> dict[str, Any]:
        """Return fields suitable for refresh_event_results, excluding identity/audit."""
        return {
            "outcome": self.outcome,
            "search_window": self.search_window,
            "no_evidence_reason": self.no_evidence_reason,
            "new_evidence_count": self.new_evidence_count,
            "indicator_count": self.indicator_count,
            "counterindicator_count": self.counterindicator_count,
            "recommended_probability": self.recommended_probability,
            "recommended_band": self.recommended_band,
            "recommended_label": self.recommended_label,
            "recommendation_confidence": self.recommendation_confidence,
            "change_from_baseline": self.change_from_baseline,
            "change_recommended": int(self.change_recommended),
            "recommendation_reason": self.recommendation_reason,
            "do_not_write": int(self.do_not_write),
            "next_step": self.next_step,
        }


def _decode_report(report: str | bytes) -> str:
    if isinstance(report, bytes):
        try:
            return report.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RefreshParseError(f"report is not valid UTF-8: {exc}") from exc
    if not isinstance(report, str):
        raise RefreshParseError("report must be str or UTF-8 bytes")
    return report


def _nonempty(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise RefreshParseError(f"{field} must not be empty")
    return value


def _parse_percent(value: str, field: str, *, allow_na: bool = False) -> int | None:
    raw = value.strip()
    if allow_na and raw.lower() == "n/a":
        return None
    if raw.endswith("%"):
        raw = raw[:-1].strip()
    try:
        number = int(raw)
    except ValueError as exc:
        raise RefreshParseError(f"{field} must be an integer percent or n/a") from exc
    if not 0 <= number <= 100:
        raise RefreshParseError(f"{field} must be between 0 and 100")
    return number


def _parse_score(value: str, field: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as exc:
        raise RefreshParseError(f"{field} must be an integer") from exc
    if not 0 <= number <= 100:
        raise RefreshParseError(f"{field} must be between 0 and 100")
    return number


def _parse_bool_true(value: str, field: str) -> bool:
    raw = value.strip().lower()
    if raw != "true":
        raise RefreshParseError(f"{field} must be true")
    return True


def _split_key_value(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    key = key.strip()
    if not key or any(ch.isspace() for ch in key):
        return None
    return key, value.strip()


def _parse_kv_block(
    lines: list[str],
    allowed_keys: tuple[str, ...],
    block_name: str,
) -> dict[str, str]:
    values: dict[str, str] = {}
    last_key: str | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        parsed = _split_key_value(line)
        if parsed and parsed[0] in allowed_keys:
            key, value = parsed
            if key in values:
                raise RefreshParseError(f"duplicate key {key} in {block_name}")
            values[key] = value
            last_key = key
            continue

        if parsed and parsed[0] not in allowed_keys:
            raise RefreshParseError(f"unexpected key {parsed[0]} in {block_name}")

        if last_key is None:
            raise RefreshParseError(f"unexpected line in {block_name}: {line}")
        values[last_key] = (values[last_key] + " " + line).strip()

    missing = [key for key in allowed_keys if key not in values]
    if missing:
        raise RefreshParseError(
            f"missing key(s) in {block_name}: {', '.join(missing)}"
        )
    return values


def _section_index(lines: list[str], marker: str) -> int:
    indexes = [i for i, line in enumerate(lines) if line.strip() == marker]
    if len(indexes) != 1:
        raise RefreshParseError(f"expected exactly one {marker} section")
    return indexes[0]


def _parse_candidates(
    lines: list[str],
) -> tuple[tuple[RefreshCandidate, ...], str | None, str]:
    meaningful = [line for line in lines if line.strip()]
    if not meaningful:
        raise RefreshParseError("CANDIDATE_EVIDENCE section is empty")

    if meaningful[0].strip() == NO_EVIDENCE:
        block = _parse_kv_block(
            meaningful[1:],
            ("reason",),
            "CANDIDATE_EVIDENCE/no-evidence",
        )
        return (), _nonempty(block["reason"], "reason"), "no_new_evidence"

    groups: list[tuple[int, list[str]]] = []
    current_ordinal: int | None = None
    current_lines: list[str] = []

    for raw in meaningful:
        line = raw.strip()
        if line.endswith(".") and line[:-1].isdigit():
            ordinal = int(line[:-1])
            if current_ordinal is not None:
                groups.append((current_ordinal, current_lines))
            current_ordinal = ordinal
            current_lines = []
            continue
        if current_ordinal is None:
            raise RefreshParseError(
                "candidate evidence must start with a numbered item such as 1."
            )
        current_lines.append(raw)

    if current_ordinal is not None:
        groups.append((current_ordinal, current_lines))

    if not groups:
        raise RefreshParseError("no candidate evidence items found")
    if len(groups) > 3:
        raise RefreshParseError("candidate evidence count exceeds maximum of 3")

    candidates: list[RefreshCandidate] = []
    for expected, (ordinal, item_lines) in enumerate(groups, start=1):
        if ordinal != expected:
            raise RefreshParseError(
                f"candidate ordinals must be sequential from 1; got {ordinal}"
            )
        data = _parse_kv_block(
            item_lines,
            CANDIDATE_KEYS,
            f"CANDIDATE_EVIDENCE/{ordinal}",
        )

        direction = data["direction"].strip().lower()
        if direction not in ALLOWED_DIRECTIONS:
            raise RefreshParseError(
                "direction must be indicator or counterindicator; "
                f"got {data['direction']!r}"
            )

        strength = data["strength"].strip().lower()
        if strength not in ALLOWED_STRENGTHS:
            raise RefreshParseError(f"invalid strength: {data['strength']!r}")

        duplicate_risk = data["duplicate_risk"].strip().lower()
        if duplicate_risk not in ALLOWED_DUPLICATE_RISK:
            raise RefreshParseError(
                f"invalid duplicate_risk: {data['duplicate_risk']!r}"
            )

        freshness = data["freshness"].strip()
        if freshness not in ALLOWED_FRESHNESS:
            raise RefreshParseError(f"invalid freshness: {freshness!r}")

        candidates.append(
            RefreshCandidate(
                ordinal=ordinal,
                direction=direction,
                strength=strength,
                relevance=_parse_score(data["relevance"], "relevance"),
                credibility=_parse_score(data["credibility"], "credibility"),
                title=_nonempty(data["title"], "title"),
                source=_nonempty(data["source"], "source"),
                url=_nonempty(data["url"], "url"),
                published_at=_nonempty(data["published_at"], "published_at"),
                summary=_nonempty(data["summary"], "summary"),
                why_it_matters=_nonempty(data["why_it_matters"], "why_it_matters"),
                duplicate_risk=duplicate_risk,
                freshness=freshness,
            )
        )

    return tuple(candidates), None, "new_evidence"


def parse_refresh_report(
    report: str | bytes,
    *,
    expected_event_id: str | None = None,
    expected_baseline_probability: int | None = None,
) -> RefreshParseResult:
    """Parse and validate one final PROROK refresh report.

    expected_event_id and expected_baseline_probability are collector-side
    snapshot checks. A mismatch is a hard parse failure.
    """

    text = _decode_report(report).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines or lines[0].strip() != HEADER:
        raise RefreshParseError(f"report must start with {HEADER}")

    candidate_idx = _section_index(lines, CANDIDATE_SECTION)
    assessment_idx = _section_index(lines, ASSESSMENT_SECTION)
    db_action_idx = _section_index(lines, DB_ACTION_SECTION)

    if not (0 < candidate_idx < assessment_idx < db_action_idx):
        raise RefreshParseError("report sections are missing or out of order")

    header = _parse_kv_block(
        lines[1:candidate_idx],
        HEADER_KEYS,
        "header",
    )

    event_id = _nonempty(header["event_id"], "event_id")
    if expected_event_id is not None and event_id != expected_event_id:
        raise RefreshParseError(
            f"event_id mismatch: expected {expected_event_id!r}, got {event_id!r}"
        )

    baseline_probability = _parse_percent(
        header["baseline_probability"],
        "baseline_probability",
    )
    assert baseline_probability is not None

    if (
        expected_baseline_probability is not None
        and baseline_probability != expected_baseline_probability
    ):
        raise RefreshParseError(
            "baseline_probability mismatch: "
            f"expected {expected_baseline_probability}, got {baseline_probability}"
        )

    search_window = _nonempty(header["search_window"], "search_window")

    candidates, no_evidence_reason, outcome = _parse_candidates(
        lines[candidate_idx + 1 : assessment_idx]
    )

    assessment = _parse_kv_block(
        lines[assessment_idx + 1 : db_action_idx],
        ASSESSMENT_KEYS,
        "ASSESSMENT_RECOMMENDATION",
    )

    recommended_probability = _parse_percent(
        assessment["recommended_probability"],
        "recommended_probability",
        allow_na=True,
    )

    raw_band = assessment["recommended_band"].strip()
    recommended_band = None if raw_band.lower() == "n/a" else raw_band
    if recommended_band is not None and recommended_band not in ALLOWED_BANDS:
        raise RefreshParseError(f"invalid recommended_band: {raw_band!r}")

    raw_label = assessment["recommended_label"].strip()
    recommended_label = (
        None
        if raw_label.lower() == "n/a"
        else _nonempty(raw_label, "recommended_label")
    )

    confidence = assessment["confidence"].strip().lower()
    if confidence not in ALLOWED_CONFIDENCE:
        raise RefreshParseError(f"invalid confidence: {assessment['confidence']!r}")

    raw_change = assessment["change_from_baseline"].strip().lower()
    # Legacy prompt exposes keep; v3 stores the canonical no_update value.
    change_from_baseline = "no_update" if raw_change == "keep" else raw_change
    if change_from_baseline not in {"increase", "decrease", "no_update"}:
        raise RefreshParseError(
            f"invalid change_from_baseline: {assessment['change_from_baseline']!r}"
        )

    rationale = _nonempty(assessment["rationale"], "rationale")

    db_action = _parse_kv_block(
        lines[db_action_idx + 1 :],
        DB_ACTION_KEYS,
        "DB_ACTION",
    )
    do_not_write = _parse_bool_true(db_action["do_not_write"], "do_not_write")
    next_step = _nonempty(db_action["next_step"], "next_step")

    if outcome == "no_new_evidence":
        if recommended_probability is not None:
            raise RefreshParseError(
                "NO_NEW_EVIDENCE_FOUND requires recommended_probability: n/a"
            )
        if recommended_band is not None:
            raise RefreshParseError(
                "NO_NEW_EVIDENCE_FOUND requires recommended_band: n/a"
            )
        if recommended_label is not None:
            raise RefreshParseError(
                "NO_NEW_EVIDENCE_FOUND requires recommended_label: n/a"
            )
        if change_from_baseline != "no_update":
            raise RefreshParseError(
                "NO_NEW_EVIDENCE_FOUND requires change_from_baseline: no_update"
            )

    if change_from_baseline in {"increase", "decrease"}:
        if recommended_probability is None:
            raise RefreshParseError(
                "increase/decrease requires a numeric recommended_probability"
            )
        if recommended_band is None or recommended_label is None:
            raise RefreshParseError(
                "increase/decrease requires recommended_band and recommended_label"
            )
    elif (
        recommended_probability is not None
        and recommended_probability != baseline_probability
    ):
        raise RefreshParseError(
            "no_update/keep cannot recommend a different probability"
        )

    return RefreshParseResult(
        event_id=event_id,
        baseline_probability=baseline_probability,
        search_window=search_window,
        outcome=outcome,
        no_evidence_reason=no_evidence_reason,
        candidates=candidates,
        recommended_probability=recommended_probability,
        recommended_band=recommended_band,
        recommended_label=recommended_label,
        recommendation_confidence=confidence,
        change_from_baseline=change_from_baseline,
        recommendation_reason=rationale,
        do_not_write=do_not_write,
        next_step=next_step,
    )
