#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import sqlite3
from collections import Counter

DB = "/data/workspace/prorok/prorok.sqlite3"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

try:
    events = conn.execute(
        """
        SELECT
            e.event_id,
            e.title,
            (
                SELECT a.probability_percent
                FROM assessments a
                WHERE a.event_id = e.event_id
                ORDER BY a.assessed_at DESC, a.assessment_id DESC
                LIMIT 1
            ) AS current_probability
        FROM events e
        WHERE e.status = 'active'
        ORDER BY e.updated_at DESC, e.event_id
        """
    ).fetchall()

    latest_refresh = conn.execute(
        """
        SELECT refresh_id, status
        FROM refresh_runs
        WHERE scope = 'all'
          AND trigger_source = 'scheduled'
        ORDER BY refresh_id DESC
        LIMIT 1
        """
    ).fetchone()

    print("\U0001F52E PROROK — ранкове оновлення")
    print("")

    if not events:
        print("Активних подій немає.")
        raise SystemExit(0)

    latest_refresh_id = latest_refresh["refresh_id"] if latest_refresh else None

    if latest_refresh and latest_refresh["status"] == "running":
        print("Оновлення PROROK ще триває. Підсумковий статус буде доступний після завершення перевірки.")
        raise SystemExit(0)

    candidate_count = 0
    if latest_refresh_id is not None:
        candidate_row = conn.execute(
            """
            SELECT COUNT(*) AS candidate_count
            FROM refresh_candidate_evidence rce
            JOIN refresh_event_results rer
              ON rer.refresh_event_result_id = rce.refresh_event_result_id
            WHERE rer.refresh_id = ?
            """,
            (latest_refresh_id,),
        ).fetchone()
        candidate_count = int(candidate_row["candidate_count"] or 0)

    decision_count = 0
    error_counts = Counter()

    terminal_states = {
        "completed",
        "schedule_failed",
        "execution_failed",
        "timeout",
        "source_missing",
        "parse_failed",
        "encoding_failed",
    }

    error_labels = {
        "schedule_failed": "помилка планування",
        "execution_failed": "помилка виконання",
        "timeout": "тайм-аут",
        "source_missing": "відсутнє джерело результату",
        "parse_failed": "помилка парсингу",
        "encoding_failed": "помилка кодування",
    }

    for idx, event in enumerate(events, start=1):
        event_id = event["event_id"]
        title = event["title"] or event_id
        probability = event["current_probability"]
        probability_text = f"{probability}%" if probability is not None else "n/a"

        print(f"{idx}. {title} — {probability_text}")

        result = None
        if latest_refresh_id is not None:
            result = conn.execute(
                """
                SELECT
                    rer.refresh_event_result_id,
                    rer.job_state,
                    rer.outcome,
                    rer.new_evidence_count,
                    rer.recommendation_valid,
                    rer.change_recommended,
                    rer.baseline_probability,
                    rer.recommended_probability
                FROM refresh_event_results rer
                WHERE rer.refresh_id = ?
                  AND rer.event_id = ?
                ORDER BY rer.refresh_event_result_id DESC
                LIMIT 1
                """,
                (latest_refresh_id, event_id),
            ).fetchone()

        if result is None:
            print("   Статус: не перевірено")
            print("")
            continue

        if result["job_state"] not in terminal_states:
            print("   Статус: перевірка ще не завершена")
            print("")
            continue

        if result["job_state"] != "completed":
            error_counts[result["job_state"]] += 1
            print("   Статус: помилка перевірки")
            print("")
            continue

        new_sources = int(result["new_evidence_count"] or 0) > 0
        recommendation_valid = int(result["recommendation_valid"] or 0) == 1
        change_recommended = int(result["change_recommended"] or 0) == 1

        decision = conn.execute(
            """
            SELECT decision_id
            FROM refresh_user_decisions
            WHERE refresh_event_result_id = ?
            LIMIT 1
            """,
            (result["refresh_event_result_id"],),
        ).fetchone()

        if not new_sources:
            print("   Нові джерела: ні · Рішення: не потрібне")
            print("")
            continue

        if not recommendation_valid:
            print("   Нові джерела: так · Рекомендація: не сформована")
            print("")
            continue

        decision_needed = change_recommended and decision is None

        if decision_needed:
            decision_count += 1
            print("   Нові джерела: так · Рішення: потрібне")
            baseline = result["baseline_probability"]
            recommended = result["recommended_probability"]
            if baseline is not None and recommended is not None:
                print(
                    f"   Рекомендація: "
                    f"{baseline}% → {recommended}%"
                )
        else:
            print("   Нові джерела: так · Рішення: не потрібне")

        print("")

    print(
        f"Потребують рішення: "
        f"{decision_count} із {len(events)}"
    )
    print(f"Нових кандидатів знайдено: {candidate_count}")

    if error_counts:
        error_parts = []
        for state in (
            "schedule_failed",
            "execution_failed",
            "timeout",
            "source_missing",
            "parse_failed",
            "encoding_failed",
        ):
            count = error_counts.get(state, 0)
            if count:
                error_parts.append(f"{error_labels[state]} — {count}")
        print("Помилки перевірки: " + "; ".join(error_parts) + ".")
    else:
        print("Помилки перевірки: немає.")

finally:
    conn.close()
PY
