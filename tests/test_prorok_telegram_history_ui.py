from pathlib import Path

PLUGIN = (
    Path(__file__).resolve().parent.parent
    / "prorok_telegram"
    / "src"
    / "index.js"
)


def test_event_history_is_paginated_for_telegram_message_limit() -> None:
    source = PLUGIN.read_text(encoding="utf-8")

    assert "const EVENT_HISTORY_PAGE_SIZE = 5;" in source
    assert "async function eventHistoryPresentation(eventId, page = 0)" in source
    assert "assessments.slice(start, start + EVENT_HISTORY_PAGE_SIZE)" in source
    assert "event-history:${token}:${safePage - 1}" in source
    assert "event-history:${token}:${safePage + 1}" in source
    assert "Invalid PROROK event-history callback payload" in source
    assert "Number.parseInt(rawPage, 10)" in source
    assert "assessments.slice(0, 12)" not in source
