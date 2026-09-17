#!/usr/bin/env python3
"""Prompt hardening for PROROK refresh search coverage."""

from __future__ import annotations

SEARCH_PROTOCOL = r"""
13. Mandatory search protocol before final report:
   - виконай щонайменше 3 окремі web/tavily search calls з різними query;
   - search #1: broad factual search за основним формулюванням події;
   - search #2: indicator search — шукай нові факти, що можуть підвищити probability;
   - search #3: counterindicator search — шукай нові факти, що можуть знизити probability;
   - для кожного search проси 5-8 результатів, якщо інструмент підтримує count;
   - якщо будь-який search повернув 0 результатів, обов'язково переформулюй запит і зроби додатковий search;
   - не використовуй несумісні або сумнівні пошукові оператори на кшталт site:news або after:DATE, якщо інструмент має окремі параметри date_after/freshness;
   - мова query має відповідати джерелам, які реально можуть висвітлювати тему; дозволено і бажано використовувати англійські, українські або російські формулювання залежно від теми;
   - окремо перевір авторитетні першоджерела/великі медіа/think tanks, якщо broad search недостатній;
   - NO_NEW_EVIDENCE_FOUND дозволено тільки після виконання цього search protocol;
   - не створюй candidate evidence лише для проходження цього правила: якщо після достатнього пошуку якісних нових evidence немає, поверни NO_NEW_EVIDENCE_FOUND.
""".strip()


def apply_search_protocol(prompt: str) -> str:
    if "Mandatory search protocol before final report:" in prompt:
        return prompt
    marker = "\nФормат фінальної відповіді:"
    if marker in prompt:
        return prompt.replace(marker, "\n\n" + SEARCH_PROTOCOL + marker, 1)
    return prompt.rstrip() + "\n\n" + SEARCH_PROTOCOL + "\n"
