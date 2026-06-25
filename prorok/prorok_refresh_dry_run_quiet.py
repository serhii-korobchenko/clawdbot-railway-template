#!/usr/bin/env python3
"""Quiet wrapper for PROROK refresh dry-run launcher.

The underlying launcher calls `openclaw cron add`, whose stdout can include the full
cron payload and therefore the full refresh prompt. This wrapper monkey-patches that
subprocess call so the Telegram command returns a compact confirmation instead of a
very large JSON object.

It also appends a strict prompt guard used by the Telegram command path: when the
refresh result is NO_NEW_EVIDENCE_FOUND, the final report must not introduce new
factual claims in reason or rationale that are not listed as candidate evidence.
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

NO_NEW_EVIDENCE_GUARD = """9. Strict no-new-evidence report rule:
   - якщо CANDIDATE_EVIDENCE дорівнює NO_NEW_EVIDENCE_FOUND, увесь блок reason і rationale має бути процедурним, а не фактологічним;
   - reason/rationale не мають вводити жодних нових фактів, назв нових подій, країн, локацій, заяв, навчань, переміщень, модернізацій, організацій або джерел, які не оформлені як candidate evidence;
   - не описуй, що саме було знайдено у свіжому пошуку, якщо це не включено в candidate evidence;
   - дозволені лише формулювання на кшталт: "нових якісних, релевантних і не дубльованих джерел не знайдено", "підстав для зміни оцінки немає", "recommended_probability: n/a", "change_from_baseline: no_update";
   - якщо хочеш згадати будь-який конкретний новий факт або тему, спершу внеси її в CANDIDATE_EVIDENCE; інакше не згадуй її взагалі;
   - для NO_NEW_EVIDENCE_FOUND rationale має пояснювати тільки причину no_update, без нового геополітичного або військового змісту.
"""


def guarded_build_prompt(*args: Any, **kwargs: Any) -> str:
    prompt = REAL_BUILD_PROMPT(*args, **kwargs)
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
