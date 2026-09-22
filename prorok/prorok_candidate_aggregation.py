#!/usr/bin/env python3
"""Deterministic aggregation for PROROK candidates from the same source.

Invariant: within one event/refresh, one canonical URL is one information signal.
If a document contains facts pointing in both directions, those facts are netted
before the source can become official evidence.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable

try:
    from .prorok_evidence_cli import canonicalize_url
except ImportError:
    from prorok_evidence_cli import canonicalize_url

_STRENGTH_WEIGHT = {None: 1, "weak": 1, "medium": 2, "strong": 3}
_DIRECTION_SIGN = {"indicator": 1, "counterindicator": -1, "neutral": 0}


def _score(candidate: dict[str, Any]) -> float:
    direction = str(candidate.get("direction") or "").strip()
    if direction not in _DIRECTION_SIGN:
        raise ValueError(f"invalid candidate direction: {direction!r}")
    strength = candidate.get("strength")
    if strength not in _STRENGTH_WEIGHT:
        raise ValueError(f"invalid candidate strength: {strength!r}")
    relevance = int(candidate.get("relevance") or 0)
    credibility = int(candidate.get("credibility") or 0)
    if not 0 <= relevance <= 100 or not 0 <= credibility <= 100:
        raise ValueError("relevance and credibility must be 0..100")
    return _DIRECTION_SIGN[direction] * _STRENGTH_WEIGHT[strength] * relevance * credibility


def _net_direction(score: float) -> str:
    if score > 0:
        return "indicator"
    if score < 0:
        return "counterindicator"
    return "neutral"


def aggregate_same_source_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one candidate per canonical URL, preserving first-source order.

    A single candidate is returned unchanged except for canonical_url metadata.
    Multiple candidates for the same URL are combined into one net signal. The
    combined row retains every component in ``source_components`` for auditability.
    """
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    canonical_urls: dict[str, str] = {}
    for raw in candidates:
        candidate = dict(raw)
        url = str(candidate.get("url") or "").strip()
        if not url:
            raise ValueError("candidate URL is required")
        canonical_url, canonical_hash, _domain = canonicalize_url(url)
        groups.setdefault(canonical_hash, []).append(candidate)
        canonical_urls[canonical_hash] = canonical_url

    result: list[dict[str, Any]] = []
    for canonical_hash, group in groups.items():
        if len(group) == 1:
            row = dict(group[0])
            row["canonical_url"] = canonical_urls[canonical_hash]
            row["source_component_count"] = 1
            result.append(row)
            continue

        scores = [_score(item) for item in group]
        total = sum(scores)
        representative = dict(max(group, key=lambda item: abs(_score(item))))
        representative["direction"] = _net_direction(total)
        representative["canonical_url"] = canonical_urls[canonical_hash]
        representative["source_component_count"] = len(group)
        representative["source_components"] = [dict(item) for item in group]
        representative["summary"] = " | ".join(
            str(item.get("summary") or "").strip() for item in group if str(item.get("summary") or "").strip()
        )
        representative["why_it_matters"] = " | ".join(
            str(item.get("why_it_matters") or "").strip() for item in group if str(item.get("why_it_matters") or "").strip()
        )
        result.append(representative)
    return result
