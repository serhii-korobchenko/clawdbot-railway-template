#!/usr/bin/env python3
"""Shared deterministic PROROK probability calibration primitives."""

from __future__ import annotations

CANONICAL_PROBABILITIES = tuple(range(0, 101, 5))
ALLOWED_PROBABILITIES = frozenset(CANONICAL_PROBABILITIES)
PROBABILITY_SCALE = (
    (0, 5, "0-5%", "Віддалена можливість"),
    (10, 20, "10-20%", "Ймовірність низька"),
    (25, 35, "25-35%", "Малоймовірно"),
    (40, 50, "40-50%", "Реалістична можливість"),
    (55, 75, "55-75%", "Ймовірно"),
    (80, 90, "80-90%", "Висока ймовірність"),
    (95, 100, "95-100%", "Майже напевно"),
)


class CalibrationValidationError(ValueError):
    pass


def probability_band(probability: int) -> tuple[str, str]:
    if isinstance(probability, bool) or not isinstance(probability, int):
        raise CalibrationValidationError("probability must be an integer")
    if not 0 <= probability <= 100:
        raise CalibrationValidationError("probability must be between 0 and 100")
    for low, high, band, label in PROBABILITY_SCALE:
        if low <= probability <= high:
            return band, label
    raise CalibrationValidationError(
        f"probability {probability}% falls in a non-canonical gap"
    )


def validate_recommendation_probability(probability: int) -> int:
    if isinstance(probability, bool) or probability not in ALLOWED_PROBABILITIES:
        raise CalibrationValidationError(
            f"recommended_probability {probability!r} is not on the canonical 5pp grid"
        )
    return probability


def calibration_math(baseline_probability: int, recommended_probability: int) -> dict[str, object]:
    # Historical baselines may be non-grid, but must still be valid percentages.
    baseline_band, _ = probability_band(baseline_probability)
    validate_recommendation_probability(recommended_probability)
    recommended_band, recommended_label = probability_band(recommended_probability)
    delta = recommended_probability - baseline_probability
    change = "increase" if delta > 0 else "decrease" if delta < 0 else "no_update"
    return {
        "recommended_band": recommended_band,
        "recommended_label": recommended_label,
        "probability_delta": delta,
        "change_from_baseline": change,
        "category_transition": recommended_band != baseline_band,
    }
