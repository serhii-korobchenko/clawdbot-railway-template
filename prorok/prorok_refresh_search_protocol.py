#!/usr/bin/env python3
"""Prompt hardening for PROROK refresh search coverage."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone


_LAST_ASSESSED_RE = re.compile(r"^last_assessed_at:\s*(.+?)\s*$", re.MULTILINE)


def _parse_last_assessed_at(prompt: str) -> datetime | None:
    match = _LAST_ASSESSED_RE.search(prompt)
    if not match:
        return None

    raw = match.group(1).strip()
    if not raw or raw.lower() in {"n/a", "na", "none", "null"}:
        return None

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw[:10])
        except ValueError:
            return None
        parsed = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            tzinfo=timezone.utc,
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _choose_tavily_time_range(
    last_assessed_at: datetime | None,
    now: datetime | None = None,
) -> str | None:
    if last_assessed_at is None:
        return None

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if last_assessed_at > current:
        return None

    age_days = (current.date() - last_assessed_at.date()).days

    if age_days <= 1:
        return "day"
    if age_days <= 7:
        return "week"
    if age_days <= 31:
        return "month"
    if age_days <= 365:
        return "year"
    return None


def _build_search_protocol(prompt: str) -> str:
    last_assessed_at = _parse_last_assessed_at(prompt)
    tavily_time_range = _choose_tavily_time_range(last_assessed_at)

    if last_assessed_at is None:
        boundary_text = "last_assessed_at не вдалося визначити як валідну дату"
        time_range_rule = (
            "не передавай time_range лише заради формального фільтра; "
            "не вигадуй часову межу"
        )
        exact_boundary = "невідома"
    else:
        exact_boundary = last_assessed_at.isoformat().replace("+00:00", "Z")
        boundary_text = f"точна межа last_assessed_at = {exact_boundary}"
        if tavily_time_range:
            time_range_rule = (
                f'для search #1, #2 і #3 передай time_range: "{tavily_time_range}" '
                "як coarse pre-filter"
            )
        else:
            time_range_rule = (
                "для search #1, #2 і #3 не передавай time_range, бо жодне з "
                "day/week/month/year не покриває всю потрібну часову межу"
            )

    return f"""
13. Mandatory search protocol before final report:
   - єдиний дозволений пошуковий інструмент для search #1, #2 і #3: tavily_search; не використовуй web_search як fallback; якщо tavily_search недоступний або повернув технічну помилку, не підмінюй його іншим search tool;
   - виконай щонайменше 3 окремі search calls з різними query;
   - search #1: broad factual search за основним формулюванням події;
   - search #2: indicator search — шукай нові факти, що можуть підвищити probability;
   - search #3: downside search — природною мовою шукай нові факти, що можуть знизити probability; НЕ використовуй у query внутрішні терміни counterindicator, counter-indicator або counter indicator; формулюй змістовно, наприклад: "signals suggesting Russia is unlikely to use nuclear weapons in Ukraine", "evidence of nuclear restraint", "absence of preparations for nuclear use" — адаптуй приклади до конкретної події, не копіюй їх механічно;
   - для кожного search #1, #2 і #3 використовуй tavily_search з topic: "news" та max_results: 7; topic=news потрібен, щоб Tavily за можливості повернув publication metadata;
   - {boundary_text};
   - {time_range_rule};
   - time_range є лише грубим pre-filter і НЕ замінює точну перевірку дати кожного результату;
   - для кожного результату tavily_search перевір поле published; тільки матеріал із published ПІСЛЯ {exact_boundary} може мати freshness: new_after_last_assessment;
   - freshness verification є обов'язковою частиною search attempt: для кожного з search #1, #2 і #3 візьми перші 3 результати, у яких URL присутній, але published відсутній або порожній, і перевір КОЖЕН такий URL через tavily_extract (бажано одним batch-викликом) або web_fetch до фінального висновку;
   - якщо published відсутній, неоднозначний або має лише дату, яка збігається з датою last_assessed_at, підтвердь точну дату/час через сторінку джерела, tavily_extract або web_fetch; якщо підтвердити не можна, не класифікуй матеріал як new_after_last_assessment;
   - матеріал із published ДО або НА межі last_assessed_at не є новим evidence; його можна розглядати лише окремо як missed_baseline_evidence, якщо він істотно змінює баланс оцінки;
   - НЕ використовуй after:DATE і НЕ використовуй жодний оператор site: у query; якщо потрібне доменне обмеження, передавай include_domains;
   - якщо будь-який із search #1, #2 або #3 повернув 0 результатів, обов'язково ПІСЛЯ нього зроби додатковий tavily_search з іншим переформульованим query; retry також має використовувати topic: "news", max_results: 7, той самий time_range policy та не містити after:DATE або site:;
   - мова query має відповідати джерелам, які реально можуть висвітлювати тему; дозволено й бажано використовувати англійські, українські або російські формулювання залежно від теми;
   - окремо перевір авторитетні першоджерела, великі медіа, think tanks або профільні інститути, якщо broad search недостатній;
   - NO_NEW_EVIDENCE_FOUND дозволено тільки після виконання search protocol, обов'язкових zero-result retries та freshness verification для описаних вище результатів без published;
   - не створюй candidate evidence лише для проходження цього правила: якщо після достатнього пошуку й перевірки якісних нових evidence немає, поверни NO_NEW_EVIDENCE_FOUND.
""".strip()


def apply_search_protocol(prompt: str) -> str:
    if "Mandatory search protocol before final report:" in prompt:
        return prompt

    search_protocol = _build_search_protocol(prompt)
    marker = "\nФормат фінальної відповіді:"

    if marker in prompt:
        return prompt.replace(marker, "\n\n" + search_protocol + marker, 1)

    return prompt.rstrip() + "\n\n" + search_protocol + "\n"
