#!/usr/bin/env python3
"""Create a one-shot OpenClaw cron job for a PROROK refresh dry-run.

The launcher reads the current event, latest assessment, and recent evidence from the
PROROK SQLite database, builds a structured no-write refresh prompt, and schedules a
one-shot Telegram delivery through `openclaw cron add`.

It intentionally does not write to the PROROK database. The resulting agent job is
also instructed not to call `/prorok add-evidence` or `/prorok assess`; human review is
required before any evidence or assessment changes are applied.
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path("/data/workspace/prorok/prorok.sqlite3")
DEFAULT_PROMPT_DIR = Path("/data/workspace/prorok/refresh_prompts")
DEFAULT_CHAT_ID = "-1003804919781"
DEFAULT_THREAD_ID = "112"
DEFAULT_AT = "2m"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_TOOLS = "tavily_search tavily_extract web_search web_fetch read"


@dataclass(frozen=True)
class EventState:
    event_id: str
    title: str
    question: str
    forecast_horizon: str
    status: str
    decision_criteria: str
    tags: str


@dataclass(frozen=True)
class AssessmentState:
    probability: str = "n/a"
    band: str = "n/a"
    label: str = "n/a"
    confidence: str = "n/a"
    assessed_at: str = "n/a"
    rationale: str = "n/a"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"PROROK database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def load_event(conn: sqlite3.Connection, event_id: str) -> EventState:
    row = conn.execute(
        """
        SELECT event_id, title, question, forecast_horizon, status, decision_criteria, tags
        FROM events
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"Event not found: {event_id}")
    return EventState(
        event_id=row["event_id"] or event_id,
        title=row["title"] or "",
        question=row["question"] or "",
        forecast_horizon=row["forecast_horizon"] or "",
        status=row["status"] or "",
        decision_criteria=row["decision_criteria"] or "",
        tags=row["tags"] or "",
    )


def load_latest_assessment(conn: sqlite3.Connection, event_id: str) -> AssessmentState:
    row = conn.execute(
        """
        SELECT
            probability_percent AS probability,
            probability_band AS band,
            probability_label AS label,
            confidence,
            assessed_at,
            rationale
        FROM assessments
        WHERE event_id = ?
        ORDER BY assessed_at DESC, assessment_id DESC
        LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return AssessmentState()
    probability = row["probability"]
    return AssessmentState(
        probability=f"{probability}%" if probability is not None else "n/a",
        band=row["band"] or "n/a",
        label=row["label"] or "n/a",
        confidence=row["confidence"] or "n/a",
        assessed_at=row["assessed_at"] or "n/a",
        rationale=row["rationale"] or "n/a",
    )


def load_evidence_lines(conn: sqlite3.Connection, event_id: str, limit: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT e.direction, e.strength, e.relevance, e.credibility, e.summary,
               s.title AS source_title, s.url, s.canonical_url, e.created_at
        FROM evidence_items e
        JOIN sources s ON s.source_id = e.source_id
        WHERE e.event_id = ?
        ORDER BY e.created_at DESC, e.evidence_id DESC
        LIMIT ?
        """,
        (event_id, limit),
    ).fetchall()
    if not rows:
        return ["No evidence rows yet."]

    lines: list[str] = []
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"{idx}. {row['direction']} | {row['strength']} | "
            f"relevance={row['relevance']} | credibility={row['credibility']} | "
            f"source={row['source_title']} | canonical_url={row['canonical_url']} | "
            f"created_at={row['created_at']} | summary={row['summary']}"
        )
    return lines


def build_prompt(event: EventState, latest: AssessmentState, evidence_lines: list[str]) -> str:
    evidence_text = "\n".join(evidence_lines)
    return f"""Виконай DRY-RUN оновлення PROROK для події {event.event_id}.

ВАЖЛИВО:
- НЕ змінюй SQLite БД.
- НЕ запускай /prorok add-evidence.
- НЕ запускай /prorok assess.
- НЕ вигадуй URL.
- Використовуй web/tavily пошук. Tavily search є основним інструментом; web_search/web_fetch використовуй як fallback.
- Поверни тільки фінальний structured report українською мовою.

Подія:
event_id: {event.event_id}
title: {event.title}
question: {event.question}
horizon: {event.forecast_horizon}
status: {event.status}
decision_criteria: {event.decision_criteria}
tags: {event.tags}

Поточна оцінка:
current_probability: {latest.probability}
current_band: {latest.band}
current_label: {latest.label}
current_confidence: {latest.confidence}
last_assessed_at: {latest.assessed_at}
latest_rationale: {latest.rationale}

Поточний evidence baseline, latest first:
{evidence_text}

Завдання пошуку:
1. Знайди тільки нові або суттєво релевантні після last_assessed_at матеріали щодо події.
2. Відібери максимум 3 candidate evidence. Краще повернути NO_NEW_EVIDENCE_FOUND, ніж слабкі або дубльовані джерела.
3. Прийнятні джерела: конкретні статті великих медіа, офіційні заяви/документи урядів або міжнародних організацій, авторитетні think tanks, профільні безпекові інститути.
4. Заборонено включати як evidence:
   - Wikipedia;
   - Medium, Substack, персональні блоги, форуми, Reddit, YouTube, TikTok, X/Twitter, Facebook;
   - SEO/evergreen сторінки без нової подієвої інформації;
   - сценарні opinion pieces без нових фактів;
   - джерела, які не можна перевірити або які містять лише загальний фон.
5. Дублювання:
   - якщо canonical_url вже є в evidence baseline, не включай це джерело;
   - якщо це переказ уже внесеної статті або того самого wire-story, не включай;
   - якщо все ж включаєш старіший матеріал як missed_baseline_evidence, duplicate_risk має бути high і треба чітко пояснити, чому він важливий.
6. Freshness rule:
   - за замовчуванням включай тільки матеріали після last_assessed_at;
   - матеріали до last_assessed_at включай лише як missed_baseline_evidence, якщо вони істотно змінюють баланс оцінки.
7. Assessment rule:
   - змінюй recommended_probability тільки якщо є нові сильні або середні evidence, які materially change the balance;
   - якщо нових якісних evidence немає, поверни change_from_baseline: no_update і recommended_probability: n/a.
8. Probability wording rule:
   - не називай band 10-20% “середньою ймовірністю”; це завжди “низька ймовірність”;
   - слово medium може стосуватися тільки confidence, strength або quality, але не probability band.

Формат фінальної відповіді:

PROROK_REFRESH_DRY_RUN
event_id: {event.event_id}
baseline_probability: {latest.probability}
search_window: <коротко>

CANDIDATE_EVIDENCE:
NO_NEW_EVIDENCE_FOUND
reason: <1-3 речення, якщо нових якісних джерел немає>

АБО, якщо є справді якісні нові джерела:

CANDIDATE_EVIDENCE:
1.
direction: indicator|counterindicator|neutral
strength: weak|medium|strong
relevance: 0-100
credibility: 0-100
title: ...
source: ...
url: ...
published_at: ...
summary: 1-2 речення
why_it_matters: 1-2 речення
duplicate_risk: low|medium|high
freshness: new_after_last_assessment|missed_baseline_evidence

ASSESSMENT_RECOMMENDATION:
recommended_probability: <число або n/a>
recommended_band: <0-5%, 10-20%, 25-35%, 40-50%, 55-75%, 80-90%, 95-100% або n/a>
recommended_label: <назва зі шкали або n/a>
confidence: low|medium|high
change_from_baseline: increase|decrease|keep|no_update
rationale: 4-7 речень

DB_ACTION:
do_not_write: true
next_step: очікує підтвердження користувача перед додаванням evidence/assessment
"""


def write_prompt(prompt_dir: Path, event_id: str, prompt: str) -> Path:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / f"refresh_{event_id}_{utc_stamp()}.md"
    path.write_text(prompt, encoding="utf-8")
    return path


def schedule_cron(args: argparse.Namespace, prompt: str) -> None:
    cmd = [
        "openclaw",
        "cron",
        "add",
        "--name",
        f"PROROK Refresh Dry Run: {args.event_id}",
        "--at",
        args.at,
        "--delete-after-run",
        "--session",
        "isolated",
        "--agent",
        args.agent,
        "--message",
        prompt,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--tools",
        args.tools,
        "--expect-final",
        "--announce",
        "--channel",
        "telegram",
        "--to",
        args.to,
        "--thread-id",
        str(args.thread_id),
    ]
    subprocess.run(cmd, check=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Schedule a no-write PROROK refresh dry-run as a one-shot OpenClaw cron job."
    )
    parser.add_argument("event_id", help="PROROK event_id to refresh in dry-run mode")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to PROROK SQLite database")
    parser.add_argument("--prompt-dir", default=str(DEFAULT_PROMPT_DIR), help="Directory for generated prompt files")
    parser.add_argument("--to", default=DEFAULT_CHAT_ID, help="Telegram chat id for delivery")
    parser.add_argument("--thread-id", default=DEFAULT_THREAD_ID, help="Telegram forum topic thread id")
    parser.add_argument("--at", default=DEFAULT_AT, help="One-shot schedule, for example 2m")
    parser.add_argument("--agent", default="main", help="OpenClaw agent id")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--tools", default=DEFAULT_TOOLS, help="Tool allow-list for the agent job")
    parser.add_argument("--no-schedule", action="store_true", help="Only write the prompt file; do not create cron job")
    parser.add_argument("--evidence-limit", type=int, default=12, help="Number of latest evidence rows to include")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    db_path = Path(args.db)
    prompt_dir = Path(args.prompt_dir)

    conn = connect(db_path)
    try:
        event = load_event(conn, args.event_id)
        latest = load_latest_assessment(conn, args.event_id)
        evidence_lines = load_evidence_lines(conn, args.event_id, args.evidence_limit)
    finally:
        conn.close()

    prompt = build_prompt(event, latest, evidence_lines)
    prompt_file = write_prompt(prompt_dir, args.event_id, prompt)
    print(f"prompt_file: {prompt_file}")

    if args.no_schedule:
        print("schedule: skipped (--no-schedule)")
        return 0

    schedule_cron(args, prompt)
    print("schedule: created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
