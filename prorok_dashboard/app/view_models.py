from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


MONTHS_UK = (
    "січ",
    "лют",
    "бер",
    "кві",
    "тра",
    "чер",
    "лип",
    "сер",
    "вер",
    "жов",
    "лис",
    "гру",
)


KNOWN_CRITERIA_LABELS = {
    "positive_resolution": "Позитивна резолюція",
    "control_duration": "Тривалість контролю",
    "confirmation": "Підтвердження",
    "event_occurs_if": "Подія відбувається, якщо",
    "does_not_count": "Не зараховується",
    "forecast_submission_deadline": "Кінцевий термін подання прогнозу",
    "resolution_timezone": "Часовий пояс резолюції",
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_date(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return value or "—"
    return f"{dt.day} {MONTHS_UK[dt.month - 1]} {dt.year}"


def format_datetime(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return value or "—"
    return (
        f"{dt.day} {MONTHS_UK[dt.month - 1]} {dt.year}"
        f" · {dt:%H:%M} UTC"
    )


def delta_text(delta: int | None) -> str:
    if delta is None:
        return ""
    if delta > 0:
        return f"+{delta} pp"
    if delta < 0:
        return f"{delta} pp"
    return "0 pp"


def delta_symbol(delta: int | None) -> str:
    if delta is None:
        return ""
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def criteria_sections(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    if criteria.get("format") != "structured":
        raw = criteria.get("raw") or ""
        return [
            {
                "key": "raw",
                "label": "Критерії резолюції",
                "kind": "text",
                "value": raw,
            }
        ] if raw else []

    data = criteria.get("data")
    if not isinstance(data, dict):
        return []

    sections: list[dict[str, Any]] = []
    for key, value in data.items():
        if value is None or value == "" or value == []:
            continue

        if isinstance(value, list):
            kind = "list"
        elif isinstance(value, dict):
            kind = "mapping"
        else:
            kind = "text"

        sections.append({
            "key": key,
            "label": KNOWN_CRITERIA_LABELS.get(
                key,
                key.replace("_", " ").strip().capitalize(),
            ),
            "kind": kind,
            "value": value,
        })

    return sections


def chart_points(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "assessed_at": item.get("assessed_at"),
            "probability": item.get("probability_percent"),
            "confidence": item.get("confidence"),
            "delta": item.get("delta_from_previous"),
        }
        for item in assessments
    ]


def safe_external_url(value: str | None) -> str:
    if not value:
        return "#"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "#"
    return value
