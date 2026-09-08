from __future__ import annotations

import pytest

from prorok.prorok_refresh_parser import (
    RefreshParseError,
    parse_refresh_report,
)


NO_EVIDENCE_REPORT = """PROROK_REFRESH_DRY_RUN
event_id: Nuclear_threat
baseline_probability: 15%
search_window: 2026-09-07..2026-09-08

CANDIDATE_EVIDENCE:
NO_NEW_EVIDENCE_FOUND
reason: Нових якісних, релевантних і не дубльованих джерел після останньої оцінки не знайдено.

ASSESSMENT_RECOMMENDATION:
recommended_probability: n/a
recommended_band: n/a
recommended_label: n/a
confidence: medium
change_from_baseline: no_update
rationale: Підстав для зміни оцінки немає.

DB_ACTION:
do_not_write: true
next_step: очікує підтвердження користувача перед додаванням evidence/assessment
"""


POSITIVE_REPORT = """PROROK_REFRESH_DRY_RUN
event_id: forecast_2026_1a9ac7533ee6
baseline_probability: 60%
search_window: після останньої оцінки

CANDIDATE_EVIDENCE:
1.
direction: indicator
strength: medium
relevance: 90
credibility: 85
title: Перше нове джерело
source: Example News
url: https://example.com/one
published_at: 2026-09-08
summary: Новий факт, що підсилює базовий сценарій.
why_it_matters: Змінює баланс evidence у бік події.
duplicate_risk: low
freshness: new_after_last_assessment
2.
direction: counterindicator
strength: weak
relevance: 70
credibility: 80
title: Друге нове джерело
source: Example Institute
url: https://example.org/two
published_at: 2026-09-08
summary: Є також фактор, що частково послаблює сценарій.
why_it_matters: Обмежує розмір рекомендованого підвищення.
duplicate_risk: low
freshness: new_after_last_assessment

ASSESSMENT_RECOMMENDATION:
recommended_probability: 65%
recommended_band: 55-75%
recommended_label: Ймовірно
confidence: medium
change_from_baseline: increase
rationale: Сукупність нових evidence помірно зміщує баланс у бік реалізації події.

DB_ACTION:
do_not_write: true
next_step: очікує підтвердження користувача перед додаванням evidence/assessment
"""


def test_parse_no_new_evidence() -> None:
    result = parse_refresh_report(
        NO_EVIDENCE_REPORT,
        expected_event_id="Nuclear_threat",
        expected_baseline_probability=15,
    )

    assert result.outcome == "no_new_evidence"
    assert result.candidates == ()
    assert result.new_evidence_count == 0
    assert result.recommended_probability is None
    assert result.recommended_band is None
    assert result.recommended_label is None
    assert result.change_from_baseline == "no_update"
    assert result.change_recommended is False
    assert result.do_not_write is True


def test_parse_positive_candidates_and_recommendation() -> None:
    result = parse_refresh_report(
        POSITIVE_REPORT,
        expected_event_id="forecast_2026_1a9ac7533ee6",
        expected_baseline_probability=60,
    )

    assert result.outcome == "new_evidence"
    assert result.new_evidence_count == 2
    assert result.indicator_count == 1
    assert result.counterindicator_count == 1
    assert result.recommended_probability == 65
    assert result.recommended_band == "55-75%"
    assert result.recommended_label == "Ймовірно"
    assert result.change_from_baseline == "increase"
    assert result.change_recommended is True
    assert result.candidates[0].url == "https://example.com/one"
    assert result.candidates[1].direction == "counterindicator"

    fields = result.event_result_fields()
    assert fields["do_not_write"] == 1
    assert fields["change_recommended"] == 1
    assert fields["new_evidence_count"] == 2


def test_multiline_value_continuation_is_preserved() -> None:
    report = POSITIVE_REPORT.replace(
        "summary: Новий факт, що підсилює базовий сценарій.",
        "summary: Новий факт, що підсилює базовий сценарій.\nДруге речення продовження.",
    )

    result = parse_refresh_report(report)
    assert result.candidates[0].summary.endswith("Друге речення продовження.")


def test_event_id_mismatch_fails_closed() -> None:
    with pytest.raises(RefreshParseError, match="event_id mismatch"):
        parse_refresh_report(
            NO_EVIDENCE_REPORT,
            expected_event_id="different_event",
        )


def test_baseline_probability_mismatch_fails_closed() -> None:
    with pytest.raises(RefreshParseError, match="baseline_probability mismatch"):
        parse_refresh_report(
            POSITIVE_REPORT,
            expected_baseline_probability=55,
        )


def test_do_not_write_false_is_rejected() -> None:
    report = POSITIVE_REPORT.replace(
        "do_not_write: true",
        "do_not_write: false",
    )

    with pytest.raises(RefreshParseError, match="do_not_write must be true"):
        parse_refresh_report(report)


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(RefreshParseError, match="not valid UTF-8"):
        parse_refresh_report(b"PROROK_REFRESH_DRY_RUN\n\xff")


def test_neutral_direction_is_rejected_until_schema_contract_changes() -> None:
    report = POSITIVE_REPORT.replace(
        "direction: indicator",
        "direction: neutral",
        1,
    )

    with pytest.raises(RefreshParseError, match="direction must be indicator"):
        parse_refresh_report(report)


def test_no_evidence_cannot_recommend_probability() -> None:
    report = NO_EVIDENCE_REPORT.replace(
        "recommended_probability: n/a",
        "recommended_probability: 20%",
    )

    with pytest.raises(
        RefreshParseError,
        match="NO_NEW_EVIDENCE_FOUND requires recommended_probability",
    ):
        parse_refresh_report(report)


def test_increase_requires_numeric_probability() -> None:
    report = POSITIVE_REPORT.replace(
        "recommended_probability: 65%",
        "recommended_probability: n/a",
    )

    with pytest.raises(
        RefreshParseError,
        match="increase/decrease requires a numeric recommended_probability",
    ):
        parse_refresh_report(report)


def test_keep_is_normalized_to_no_update_when_probability_is_unchanged() -> None:
    report = POSITIVE_REPORT.replace(
        "recommended_probability: 65%",
        "recommended_probability: 60%",
    ).replace(
        "change_from_baseline: increase",
        "change_from_baseline: keep",
    )

    result = parse_refresh_report(report)
    assert result.change_from_baseline == "no_update"
    assert result.change_recommended is False


def test_no_update_cannot_change_probability() -> None:
    report = POSITIVE_REPORT.replace(
        "change_from_baseline: increase",
        "change_from_baseline: no_update",
    )

    with pytest.raises(
        RefreshParseError,
        match="no_update/keep cannot recommend a different probability",
    ):
        parse_refresh_report(report)
