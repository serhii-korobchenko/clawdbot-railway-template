from __future__ import annotations

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "prorok"
    / "prorok_daily_status.sh"
)


def test_daily_status_counts_candidates_from_latest_refresh() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "SELECT COUNT(*) AS candidate_count" in source
    assert "FROM refresh_candidate_evidence rce" in source
    assert "JOIN refresh_event_results rer" in source
    assert "WHERE rer.refresh_id = ?" in source
    assert 'print(f"Нових кандидатів знайдено: {candidate_count}")' in source


def test_daily_status_aggregates_terminal_error_reasons_once() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "error_counts = Counter()" in source
    assert 'error_counts[result["job_state"]] += 1' in source
    assert '"schedule_failed": "помилка планування"' in source
    assert '"execution_failed": "помилка виконання"' in source
    assert '"timeout": "тайм-аут"' in source
    assert '"source_missing": "відсутнє джерело результату"' in source
    assert '"parse_failed": "помилка парсингу"' in source
    assert '"encoding_failed": "помилка кодування"' in source
    assert 'print("Помилки перевірки: " + "; ".join(error_parts) + ".")' in source
    assert 'print("Помилки перевірки: немає.")' in source


def test_daily_status_keeps_per_event_error_line_generic() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'print("   Статус: помилка перевірки")' in source
    assert source.count("Помилки перевірки:") == 2
