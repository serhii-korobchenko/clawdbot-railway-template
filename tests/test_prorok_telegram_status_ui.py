from __future__ import annotations

from pathlib import Path


PLUGIN = (
    Path(__file__).resolve().parent.parent
    / "prorok_telegram"
    / "src"
    / "index.js"
)


def test_telegram_status_management_is_deterministic_and_confirmed() -> None:
    source = PLUGIN.read_text(encoding="utf-8")

    assert 'const STATUS_CLI = process.env.PROROK_STATUS_CLI' in source
    assert 'statusTransitionLabel(event.status, toStatus)' in source
    assert 'status-apply:' in source
    assert 'async function statusConfirmationPresentation' in source
    assert 'async function appliedStatusPresentation' in source
    assert 'await runStatusCli(' in source
    assert '"--from-status"' in source
    assert '"--to-status"' in source
    assert 'if (payload.startsWith("status-apply:"))' in source
    assert 'if (payload.startsWith("status:"))' in source


def test_resolved_has_no_lifecycle_buttons() -> None:
    source = PLUGIN.read_text(encoding="utf-8")
    assert 'if (status === "archived") return [["active", "success"]];' in source
    assert 'return [];' in source


def test_selected_evidence_recommendation_ui_is_wired():
    source=(ROOT/"prorok_telegram"/"src"/"index.js").read_text(encoding="utf-8")
    assert "PROROK_EVIDENCE_RECOMMENDATION_CLI" in source
    assert "🤖 #${item.evidence_id} · Отримати рекомендацію" in source
    assert "🤖 Отримати рекомендацію" in source
    assert 'evidence-rec-apply:' in source
    assert 'evidence-rec-custom:' in source
    assert '"--recommendation-id"' in source
    assert "Official forecast не змінено." in source


def test_evidence_recommendation_cli_supports_json_output():
    source=(ROOT/"prorok"/"prorok_evidence_recommendation_cli.py").read_text(encoding="utf-8")
    assert '"--output-json"' in source
    assert "evidence_assessment_recommendation_id" in source
