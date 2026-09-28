"""Regression coverage for the PROROK pre-report freshness checkpoint."""

from prorok.prorok_refresh_search_protocol import apply_search_protocol


def test_checkpoint_is_after_search_rules_and_before_final_report():
    prompt = "search_after_at: 2026-09-26T05:25:09Z\n\nФормат фінальної відповіді:\nPROROK_REFRESH_DRY_RUN"
    result = apply_search_protocol(prompt)
    assert result.index("13. Mandatory search protocol") < result.index("14. Mandatory pre-report freshness checkpoint")
    assert result.index("14. Mandatory pre-report freshness checkpoint") < result.index("Формат фінальної відповіді:")
    assert "перші 3 результати" in result
    assert "перших трьох tavily_search calls" in result
    assert "tavily_extract (можна batch urls) або web_fetch" in result
    assert "навіть якщо збираєшся повернути NO_NEW_EVIDENCE_FOUND" in result


def test_protocol_injection_is_idempotent():
    prompt = "search_after_at: 2026-09-26T05:25:09Z\n\nФормат фінальної відповіді:"
    once = apply_search_protocol(prompt)
    assert apply_search_protocol(once) == once
    assert once.count("14. Mandatory pre-report freshness checkpoint") == 1
