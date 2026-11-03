from __future__ import annotations

import json
from pathlib import Path

from prorok import prorok_refresh_collector_v5 as v5
from prorok import prorok_refresh_collector_v6 as v6
from prorok import prorok_refresh_collector_v7 as v7
from prorok import prorok_refresh_dry_run_cron as refresh_cron
from prorok.prorok_refresh_search_protocol import apply_search_protocol


DATE_AFTER = "2026-10-18"


def _search_call(call_id: str, query: str) -> dict:
    return {
        "type": "toolCall",
        "name": "web_search",
        "id": call_id,
        "arguments": {
            "query": query,
            "count": 7,
            "date_after": DATE_AFTER,
        },
    }


def _tool_result(call_id: str, payload: dict) -> dict:
    return {
        "role": "toolResult",
        "toolCallId": call_id,
        "content": json.dumps(payload),
    }


def _write_session(path: Path, *, first_payload: dict | None = None, add_retry: bool = False) -> None:
    rows = [
        {
            "role": "assistant",
            "content": [
                _search_call("s1", "Russia nuclear weapons Ukraine developments"),
                _search_call("s2", "signals increasing nuclear use risk Ukraine"),
                _search_call("s3", "signals suggesting nuclear restraint in Ukraine"),
            ],
        },
        _tool_result(
            "s1",
            first_payload
            if first_payload is not None
            else {
                "kind": "results",
                "count": 1,
                "results": [
                    {
                        "url": "https://example.com/a",
                        "published": "2026-10-19T08:00:00Z",
                    }
                ],
            },
        ),
        _tool_result(
            "s2",
            {
                "kind": "results",
                "count": 1,
                "results": [
                    {
                        "url": "https://example.com/b",
                        "published": "2026-10-19T09:00:00Z",
                    }
                ],
            },
        ),
        _tool_result(
            "s3",
            {
                "kind": "results",
                "count": 1,
                "results": [
                    {
                        "url": "https://example.com/c",
                        "published": "2026-10-19T10:00:00Z",
                    }
                ],
            },
        ),
    ]
    if add_retry:
        rows.extend(
            [
                {
                    "role": "assistant",
                    "content": [
                        _search_call(
                            "s4",
                            "latest reporting on Russian nuclear posture toward Ukraine",
                        )
                    ],
                },
                _tool_result(
                    "s4",
                    {
                        "kind": "results",
                        "count": 1,
                        "results": [
                            {
                                "url": "https://example.com/d",
                                "published": "2026-10-19T11:00:00Z",
                            }
                        ],
                    },
                ),
            ]
        )
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_prompt_uses_openclaw_web_search_contract() -> None:
    prompt = (
        "search_after_at: 2026-10-18T05:20:00Z\n\n"
        "Формат фінальної відповіді:\nPROROK_REFRESH_DRY_RUN"
    )
    result = apply_search_protocol(prompt)

    assert "web_search" in result
    assert "count: 7" in result
    assert 'date_after: "2026-10-18"' in result
    assert "web_fetch" in result
    assert "tavily_search" not in result
    assert "tavily_extract" not in result
    assert "НЕ передавай параметр freshness" in result
    assert "date_after/date_before" in result


def test_base_refresh_prompt_is_provider_agnostic_and_probability_grid_strict() -> None:
    event = refresh_cron.EventState(
        event_id="test_event",
        title="Test",
        question="Will the test event occur?",
        forecast_horizon="2026-12-31",
        status="active",
        decision_criteria="{}",
        tags="[]",
    )
    latest = refresh_cron.AssessmentState(
        probability="25%",
        band="25-35%",
        label="Малоймовірно",
        confidence="medium",
        assessed_at="2026-10-18T05:20:00Z",
        rationale="baseline",
    )

    result = refresh_cron.build_prompt(
        event,
        latest,
        ["No evidence rows yet."],
        "2026-10-18T05:20:00Z",
    )

    assert refresh_cron.DEFAULT_TOOLS == "web_search web_fetch read"
    assert "тільки web_search" in result
    assert "provider-specific search tools не використовуй" in result
    assert "tavily_search" not in result
    assert "tavily_extract" not in result
    assert "27%, 43%, 62%" in result
    assert "канонічною сіткою" in result


def test_v5_accepts_three_brave_web_search_calls() -> None:
    calls = [
        {
            "tool": "web_search",
            "query": f"query {i}",
            "args": {"count": 7, "date_after": DATE_AFTER},
        }
        for i in range(3)
    ]

    valid, reason = v5._evaluate_v5_search_quality(calls, DATE_AFTER)

    assert valid is True
    assert "web_search" in reason


def test_v5_rejects_provider_specific_tavily_call() -> None:
    calls = [
        {
            "tool": "web_search",
            "query": "query 1",
            "args": {"count": 7, "date_after": DATE_AFTER},
        },
        {
            "tool": "tavily_search",
            "query": "query 2",
            "args": {"count": 7, "date_after": DATE_AFTER},
        },
        {
            "tool": "web_search",
            "query": "query 3",
            "args": {"count": 7, "date_after": DATE_AFTER},
        },
    ]

    valid, reason = v5._evaluate_v5_search_quality(calls, DATE_AFTER)

    assert valid is False
    assert "tool must be web_search" in reason


def test_v6_accepts_structured_web_search_results(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(session)

    valid, reason, calls = v6._evaluate_v6_search_quality(session, DATE_AFTER)

    assert valid is True
    assert len(calls) == 3
    assert "web_search" in reason


def test_v6_fails_closed_on_web_search_error_payload(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(
        session,
        first_payload={
            "kind": "error",
            "error": "quota_exhausted",
        },
    )

    valid, reason, _calls = v6._evaluate_v6_search_quality(session, DATE_AFTER)

    assert valid is False
    assert "tool returned error" in reason
    assert "quota_exhausted" in reason


def test_v7_accepts_valid_retry_after_zero_result(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(
        session,
        first_payload={
            "kind": "results",
            "count": 0,
            "results": [],
        },
        add_retry=True,
    )

    valid, reason, calls = v7._evaluate_v7_search_quality(session, DATE_AFTER)

    assert valid is True
    assert len(calls) == 4
    assert "1 zero-result core search" in reason
