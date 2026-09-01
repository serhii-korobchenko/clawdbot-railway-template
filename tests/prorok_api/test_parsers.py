from prorok_api.parsers import parse_decision_criteria, parse_tags


def test_structured_and_legacy_criteria():
    structured = parse_decision_criteria('{"event_occurs_if":["x"]}')
    assert structured["format"] == "structured"
    assert structured["data"]["event_occurs_if"] == ["x"]

    legacy = parse_decision_criteria("Legacy text")
    assert legacy == {
        "format": "text",
        "data": None,
        "raw": "Legacy text",
    }


def test_tags_defensive_fallback():
    assert parse_tags('["a","b"]') == ["a", "b"]
    assert parse_tags("not-json") == []
    assert parse_tags('{"a":1}') == []
