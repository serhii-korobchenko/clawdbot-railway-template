import json
from datetime import datetime, timezone

from prorok.prorok_logging import cleanup_old_logs, redact, write_log


def test_redact_nested_secrets_and_url_tokens():
    value = {
        "api_key": "secret-value",
        "nested": {"Authorization": "Bearer abc.def"},
        "url": "https://example.test/a?token=secret&x=1",
        "safe": "hello",
    }
    got = redact(value)
    assert got["api_key"] == "[REDACTED]"
    assert got["nested"]["Authorization"] == "[REDACTED]"
    assert "secret" not in got["url"]
    assert "token=[REDACTED]" in got["url"]
    assert got["safe"] == "hello"


def test_write_log_jsonl(tmp_path):
    path = write_log(
        "search.tool_call",
        component="test",
        log_dir=tmp_path,
        event_id="event-1",
        tool="tavily_search",
        args={"query": "test", "api_key": "secret"},
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["event"] == "search.tool_call"
    assert row["event_id"] == "event-1"
    assert row["args"]["query"] == "test"
    assert row["args"]["api_key"] == "[REDACTED]"


def test_cleanup_old_logs_keeps_retention_boundary(tmp_path):
    (tmp_path / "prorok-2026-08-24.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "prorok-2026-08-25.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "unrelated.jsonl").write_text("{}\n", encoding="utf-8")

    removed = cleanup_old_logs(
        tmp_path,
        retention_days=30,
        now=datetime(2026, 9, 24, tzinfo=timezone.utc),
    )

    assert removed == 1
    assert not (tmp_path / "prorok-2026-08-24.jsonl").exists()
    assert (tmp_path / "prorok-2026-08-25.jsonl").exists()
    assert (tmp_path / "unrelated.jsonl").exists()



def test_redact_high_confidence_secrets_but_keep_diagnostic_ids():
    value = {
        "text": (
            "query=Ukraine session_id=6ec025a8-7fbe-4e37-be36-bcb5f9499c31 "
            "api_key=TOPSECRET password: hunter2 "
            "Bearer abc.def.ghi sk-1234567890abcdefghijkl"
        )
    }
    got = redact(value)["text"]
    assert "query=Ukraine" in got
    assert "6ec025a8-7fbe-4e37-be36-bcb5f9499c31" in got
    assert "TOPSECRET" not in got
    assert "hunter2" not in got
    assert "abc.def.ghi" not in got
    assert "sk-1234567890abcdefghijkl" not in got
