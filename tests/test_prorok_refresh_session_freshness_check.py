"""Regression tests for the standalone read-only PROROK freshness audit."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prorok"))
from prorok_refresh_session_freshness_check import check_freshness


def transcript(tmp_path, verify=False, published=None):
    path = tmp_path / "session.jsonl"
    records = []
    for i in range(3):
        cid = f"search-{i}"
        records.append({"message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": cid, "name": "tavily_search", "arguments": {"query": f"query {i}"}}]}})
        rows = [{"url": "https://example.org/undated", "published": published}] if i == 2 else []
        records.append({"message": {"role": "toolResult", "toolCallId": cid,
            "content": [{"type": "text", "text": json.dumps({"results": rows})}]}})
    if verify:
        records.append({"message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "verify-1", "name": "tavily_extract",
             "arguments": {"urls": ["https://example.org/undated"]}}]}})
    path.write_text("\n".join(json.dumps(x) for x in records) + "\n", encoding="utf-8")
    return path


def test_missing_required_verification_fails(tmp_path):
    valid, reason, count = check_freshness(transcript(tmp_path))
    assert not valid
    assert count == 1
    assert "missing freshness verification" in reason
    assert "example.org" not in reason


def test_later_extract_passes(tmp_path):
    valid, _, count = check_freshness(transcript(tmp_path, verify=True))
    assert valid
    assert count == 1


def test_all_dated_needs_no_extract(tmp_path):
    valid, _, count = check_freshness(transcript(tmp_path, published="2026-09-28T00:00:00Z"))
    assert valid
    assert count == 0


def test_missing_search_result_fails_closed(tmp_path):
    path = transcript(tmp_path)
    rows = path.read_text().splitlines()
    path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    valid, reason, _ = check_freshness(path)
    assert not valid
    assert "tool result payload is missing" in reason
