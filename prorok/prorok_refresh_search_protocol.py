#!/usr/bin/env python3
"""Prompt hardening for PROROK refresh search coverage."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone


_LAST_ASSESSED_RE = re.compile(r"^last_assessed_at:\s*(.+?)\s*$", re.MULTILINE)
_SEARCH_AFTER_RE = re.compile(r"^search_after_at:\s*(.+?)\s*$", re.MULTILINE)


def _parse_datetime_field(prompt: str, pattern: re.Pattern[str]) -> datetime | None:
    match = pattern.search(prompt)
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


def _parse_last_assessed_at(prompt: str) -> datetime | None:
    return _parse_datetime_field(prompt, _LAST_ASSESSED_RE)


def _parse_search_after_at(prompt: str) -> datetime | None:
    return _parse_datetime_field(prompt, _SEARCH_AFTER_RE) or _parse_last_assessed_at(prompt)


def _build_search_protocol(prompt: str) -> str:
    search_after_at = _parse_search_after_at(prompt)

    if search_after_at is None:
        boundary_text = "search_after_at/last_assessed_at не вдалося визначити як валідну дату"
        date_filter_rule = (
            "не передавай date_after; не вигадуй часову межу"
        )
        exact_boundary = "невідома"
    else:
        exact_boundary = search_after_at.isoformat().replace("+00:00", "Z")
        date_after = search_after_at.date().isoformat()
        boundary_text = f"точна межа search_after_at = {exact_boundary}"
        date_filter_rule = (
            f'для search #1, #2 і #3 передай date_after: "{date_after}" '
            "як coarse pre-filter"
        )

    return f"""
13. Mandatory search protocol before final report:
   - єдиний дозволений пошуковий інструмент для search #1, #2 і #3: web_search; не використовуй provider-specific search tools як fallback;
   - web_search має працювати через налаштований OpenClaw provider;
   - виконай щонайменше 3 окремі search calls з різними query;
   - search #1: broad factual search за основним формулюванням події;
   - search #2: indicator search — шукай нові факти, що можуть підвищити probability;
   - search #3: downside search — природною мовою шукай нові факти, що можуть знизити probability; НЕ використовуй у query внутрішні терміни counterindicator, counter-indicator або counter indicator;
   - для кожного search #1, #2 і #3 використовуй web_search з count: 7;
   - {boundary_text};
   - {date_filter_rule};
   - date_after є лише грубим pre-filter і НЕ замінює точну перевірку published кожного результату;
   - для кожного результату web_search перевір поле published; тільки матеріал із published ПІСЛЯ {exact_boundary} може мати freshness: new_after_last_assessment; назва freshness є legacy schema label, фактична operational boundary — search_after_at;
   - freshness verification є обов'язковою частиною search attempt: для кожного з search #1, #2 і #3 візьми перші 3 результати, у яких URL присутній, але published відсутній або порожній, і перевір КОЖЕН такий URL через web_fetch до фінального висновку;
   - якщо published відсутній, неоднозначний або має лише дату, яка збігається з датою search_after_at, підтвердь точну дату/час через сторінку джерела за допомогою web_fetch; якщо підтвердити не можна, не класифікуй матеріал як new_after_last_assessment;
   - матеріал із published ДО або НА межі search_after_at не є новим evidence; його можна розглядати лише окремо як missed_baseline_evidence, якщо він істотно змінює баланс оцінки;
   - правило чистоти query застосовується ДО КОЖНОГО web_search call без винятків: до search #1/#2/#3, zero-result retry, authority/source-specific checks і будь-яких додаткових пошуків;
   - поле query має містити тільки природномовні тематичні слова; НЕ вставляй URL, hostname/domain name або будь-який search-engine field qualifier у форматі key:value;
   - НЕ використовуй after:DATE або site:DOMAIN у query; часову межу задавай тільки через date_after та подальшу перевірку published;
   - якщо потрібно окремо перевірити Reuters, AP, ISW чи інше авторитетне джерело, назви організацію природною мовою у query без hostname/domain token;
   - перед КОЖНИМ web_search виконай внутрішній parameter self-check: query не повинен містити URL/hostname/domain token, конструкцію з двокрапкою як search qualifier або інший search-engine operator; якщо містить — перепиши query природною мовою ДО виклику tool;
   - якщо будь-який із search #1, #2 або #3 повернув kind="error" або іншу технічну помилку, не трактуй це як 0 результатів і не формуй NO_NEW_EVIDENCE_FOUND; завершуй звіт технічною помилкою;
   - якщо будь-який із search #1, #2 або #3 успішно повернув kind="results", але count=0, обов'язково ПІСЛЯ нього зроби додатковий web_search з іншим переформульованим query; retry також має використовувати count: 7, той самий date_after policy та пройти той самий parameter self-check;
   - мова query має відповідати джерелам, які реально можуть висвітлювати тему; дозволено й бажано використовувати англійські, українські або російські формулювання залежно від теми;
   - NO_NEW_EVIDENCE_FOUND дозволено тільки після виконання search protocol, обов'язкових zero-result retries та freshness verification для описаних вище результатів без published;
   - не створюй candidate evidence лише для проходження цього правила: якщо після достатнього пошуку й перевірки якісних нових evidence немає, поверни NO_NEW_EVIDENCE_FOUND.

14. Mandatory pre-report freshness checkpoint (perform AFTER all searches, BEFORE composing the final report):
   - переглянь фактичні tool results перших трьох web_search calls, не покладайся на пам'ять чи текст майбутнього звіту;
   - переконайся, що кожен із перших трьох tool results має kind="results"; kind="error", kind="answer" або kind="raw" не вважається валідним structured search result для PROROK;
   - для КОЖНОГО з цих трьох searches визнач перші 3 результати з URL та порожнім/відсутнім published; дедуплікуй URL між searches, але не пропускай жодного required URL;
   - перевір, що ПІСЛЯ відповідного search для кожного required URL уже був виклик web_fetch з цим URL; сам по собі пошук, припущення щодо дати або відмова від використання джерела НЕ замінює verification call;
   - якщо хоч один required URL ще не перевірений, виконай відсутні web_fetch calls ЗАРАЗ, навіть якщо збираєшся повернути NO_NEW_EVIDENCE_FOUND або джерело нерелевантне;
   - лише після завершення всіх verification calls переходь до фінального PROROK_REFRESH_DRY_RUN; не додавай checkpoint чи перелік URL до фінальної відповіді.
""".strip()

def apply_search_protocol(prompt: str) -> str:
    if "Mandatory search protocol before final report:" in prompt:
        return prompt

    search_protocol = _build_search_protocol(prompt)
    marker = "\nФормат фінальної відповіді:"

    if marker in prompt:
        return prompt.replace(marker, "\n\n" + search_protocol + marker, 1)

    return prompt.rstrip() + "\n\n" + search_protocol + "\n"
