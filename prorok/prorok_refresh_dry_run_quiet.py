#!/usr/bin/env python3
"""Quiet wrapper for PROROK refresh dry-run launcher.

The underlying launcher calls `openclaw cron add`, whose stdout can include the full
cron payload and therefore the full refresh prompt. This wrapper monkey-patches that
subprocess call so the Telegram command returns a compact confirmation instead of a
very large JSON object.

It also appends a strict prompt guard used by the Telegram command path: when the
refresh result is NO_NEW_EVIDENCE_FOUND, the final report must not introduce new
factual claims in reason or rationale that are not listed as candidate evidence.

Railway deploy trigger: 2026-08-26T15:57Z.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import prorok_refresh_dry_run_cron as launcher


SUMMARY: dict[str, str] = {}
REAL_RUN = subprocess.run
REAL_BUILD_PROMPT = launcher.build_prompt

NO_EVIDENCE_REASON = (
    "Нових якісних, релевантних і не дубльованих джерел після останньої оцінки не знайдено. "
    "Підстав для зміни оцінки немає."
)
NO_EVIDENCE_RATIONALE = (
    "Оскільки candidate evidence відсутні, recommended_probability залишається n/a, "
    "change_from_baseline: no_update. SQLite DB не змінюється до ручного підтвердження користувача."
)
NO_EVIDENCE_NEXT_STEP = "очікує підтвердження користувача перед додаванням evidence/assessment"


def no_evidence_report_skeleton() -> str:
    """Return the mandatory final structure for a no-new-evidence report."""
    return f"""PROROK_REFRESH_DRY_RUN
event_id: <залиш event_id з події без змін>
baseline_probability: <залиш baseline_probability з поточної оцінки без змін>
search_window: <коротко>

CANDIDATE_EVIDENCE:
NO_NEW_EVIDENCE_FOUND
reason: {NO_EVIDENCE_REASON}

ASSESSMENT_RECOMMENDATION:
recommended_probability: n/a
recommended_band: n/a
recommended_label: n/a
confidence: medium
change_from_baseline: no_update
rationale: {NO_EVIDENCE_RATIONALE}

DB_ACTION:
do_not_write: true
next_step: {NO_EVIDENCE_NEXT_STEP}"""


NO_NEW_EVIDENCE_GUARD = f"""9. Strict no-new-evidence report rule:
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, увесь блок reason і rationale має бути процедурним, а не фактологічним;
   - reason/rationale не мають вводити жодних нових фактів, назв нових подій, країн, локацій, заяв, навчань, переміщень, модернізацій, організацій або джерел, які не оформлені як candidate evidence;
   - не описуй, що саме було знайдено у свіжому пошуку, якщо це не включено в candidate evidence;
   - не згадуй тему події, країни, фронт, ядерну зброю, обласні центри, поточну ситуацію, заяви, навчання, військові дії або будь-який інший конкретний зміст у reason/rationale, якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND;
   - якщо хочеш згадати будь-який конкретний новий факт або тему, спершу внеси її в CANDIDATE_EVIDENCE; інакше не згадуй її взагалі;
   - для NO_NEW_EVIDENCE_FOUND rationale має пояснювати тільки причину no_update, без нового геополітичного або військового змісту.

10. Mandatory NO_NEW_EVIDENCE_FOUND template override:
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, використовуй такі значення ДОСЛІВНО;
   - не розширюй, не перефразовуй і не додавай жодного речення до цих двох полів;
   - це правило має пріоритет над будь-якою іншою інструкцією щодо стилю, деталізації або пояснення;
   - фінальна відповідь вважається невалідною, якщо reason або rationale відрізняються від шаблону хоча б одним додатковим реченням.

   reason: {NO_EVIDENCE_REASON}

   rationale: {NO_EVIDENCE_RATIONALE}

11. Mandatory full NO_NEW_EVIDENCE_FOUND report skeleton:
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, фінальна відповідь ОБОВʼЯЗКОВО має містити всі блоки нижче;
   - не скорочуй, не стискай і не викидай ASSESSMENT_RECOMMENDATION або DB_ACTION;
   - не став rationale у CANDIDATE_EVIDENCE; rationale має бути тільки в ASSESSMENT_RECOMMENDATION;
   - не додавай NO_NEW_EVIDENCE_VALIDATION у фінальну відповідь;
   - event_id і baseline_probability підстав з цієї події, але всю решту no-evidence skeleton збережи без структурних змін.

{no_evidence_report_skeleton()}

12. Final self-check before sending:
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, перед відповіддю перевір, що рядок reason повністю збігається з Mandatory template;
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, перед відповіддю перевір, що рядок rationale повністю збігається з Mandatory template;
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, перед відповіддю перевір, що відповідь містить окремі блоки ASSESSMENT_RECOMMENDATION і DB_ACTION;
   - якщо є відхилення, виправ відповідь на повний no-evidence skeleton і тільки після цього відправляй фінальну відповідь.
"""


def no_evidence_branch_template() -> str:
    """Return the no-evidence branch to inject into the final answer format."""
    return f"""CANDIDATE_EVIDENCE:
NO_NEW_EVIDENCE_FOUND
reason: якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, використай дослівно: {NO_EVIDENCE_REASON}

ASSESSMENT_RECOMMENDATION:
recommended_probability: n/a
recommended_band: n/a
recommended_label: n/a
confidence: medium
change_from_baseline: no_update
rationale: якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, використай дослівно: {NO_EVIDENCE_RATIONALE}

DB_ACTION:
do_not_write: true
next_step: {NO_EVIDENCE_NEXT_STEP}"""


def harden_no_evidence_format(prompt: str) -> str:
    """Make the final report format itself repeat the no-evidence template.

    The base launcher already has generic placeholders in the final format. Some
    model runs follow those placeholders more strongly than the added guard, so this
    wrapper rewrites those placeholders into explicit conditional template lines and
    expands the no-evidence branch into a full report skeleton.
    """
    no_evidence_original = (
        "CANDIDATE_EVIDENCE:\n"
        "NO_NEW_EVIDENCE_FOUND\n"
        "reason: <1-3 речення, якщо нових якісних джерел немає>\n\n"
        "АБО, якщо є справді якісні нові джерела:"
    )
    no_evidence_hardened = no_evidence_branch_template() + "\n\nАБО, якщо є справді якісні нові джерела:"
    prompt = prompt.replace(no_evidence_original, no_evidence_hardened)

    reason_placeholder = "reason: <1-3 речення, якщо нових якісних джерел немає>"
    reason_hardened = (
        "reason: якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, використай дослівно: "
        f"{NO_EVIDENCE_REASON}"
    )

    rationale_placeholder = "rationale: 4-7 речень"
    rationale_hardened = (
        "rationale: якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, використай дослівно: "
        f"{NO_EVIDENCE_RATIONALE}; якщо є candidate evidence, тоді дай 4-7 речень аналізу"
    )

    prompt = prompt.replace(reason_placeholder, reason_hardened)
    prompt = prompt.replace(rationale_placeholder, rationale_hardened)

    # Add an extra validation note immediately before the first DB_ACTION in the final format.
    validation_note = (
        "\nNO_NEW_EVIDENCE_VALIDATION:\n"
        f"якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, reason MUST_EQUAL: {NO_EVIDENCE_REASON}\n"
        f"якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, rationale MUST_EQUAL: {NO_EVIDENCE_RATIONALE}\n"
        "якщо CANDIDATE_EVIDENCE = NO_NEW_EVIDENCE_FOUND, response MUST_INCLUDE окремі блоки ASSESSMENT_RECOMMENDATION і DB_ACTION.\n"
        "не додавай цей validation block у фінальну відповідь; використай його тільки як self-check.\n"
    )
    marker = "\nDB_ACTION:\n"
    if "NO_NEW_EVIDENCE_VALIDATION:" not in prompt and marker in prompt:
        prompt = prompt.replace(marker, validation_note + marker, 1)
    return prompt


def guarded_build_prompt(*args: Any, **kwargs: Any) -> str:
    prompt = REAL_BUILD_PROMPT(*args, **kwargs)
    prompt = harden_no_evidence_format(prompt)
    marker = "\nФормат фінальної відповіді:"
    if NO_NEW_EVIDENCE_GUARD in prompt:
        return prompt
    if marker in prompt:
        return prompt.replace(marker, "\n" + NO_NEW_EVIDENCE_GUARD + marker, 1)
    return prompt + "\n\n" + NO_NEW_EVIDENCE_GUARD


def _extract_summary(stdout: str) -> dict[str, str]:
    text = (stdout or "").strip()
    if not text:
        return {}

    decoder = json.JSONDecoder()
    data: Any = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
            break
        except json.JSONDecodeError:
            continue

    if not isinstance(data, dict):
        return {}

    schedule = data.get("schedule")
    run_at = ""
    if isinstance(schedule, dict):
        run_at = str(schedule.get("at") or schedule.get("cron") or "")

    return {
        "cron_id": str(data.get("id") or ""),
        "run_at": run_at,
    }


def quiet_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
    if isinstance(cmd, list) and cmd[:3] == ["openclaw", "cron", "add"]:
        proc = REAL_RUN(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        SUMMARY.update(_extract_summary(proc.stdout or ""))
        if kwargs.get("check") and proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "").strip()
            if details:
                print(details[-2000:], file=sys.stderr)
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
        return proc
    return REAL_RUN(cmd, *args, **kwargs)


def main(argv: list[str]) -> int:
    launcher.subprocess.run = quiet_run
    launcher.build_prompt = guarded_build_prompt
    result = launcher.main(argv)
    if result == 0:
        if SUMMARY.get("cron_id"):
            print(f"cron_id: {SUMMARY['cron_id']}")
        if SUMMARY.get("run_at"):
            print(f"run_at: {SUMMARY['run_at']}")
    return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
