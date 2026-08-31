#!/usr/bin/env python3
"""Create or update a PROROK event from a forecast-question screenshot.

This helper is intentionally deterministic around the database layer: the only
model-dependent step is image-to-JSON extraction. Event and assessment writes are
performed through the existing PROROK CLI helpers.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_HOME = "/data/workspace/prorok"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_AUDIT_LOG = "/data/workspace/prorok/screenshot_ingest.jsonl"
VALID_CONFIDENCE = {"low", "medium", "high"}

EXTRACTION_PROMPT = """You extract PROROK forecast-event data from a screenshot.

Return ONLY valid JSON with this shape:
{
  "is_forecast_event": true,
  "event_id": "stable_ascii_snake_case_id_with_year",
  "title": "short Ukrainian title",
  "question": "full binary forecast question in Ukrainian",
  "forecast_horizon": "YYYY-MM-DD",
  "resolution_timezone": "CET or UTC or local timezone if visible",
  "forecast_submission_deadline": "YYYY-MM-DD or null",
  "event_occurs_if": ["resolution criterion 1", "resolution criterion 2"],
  "does_not_count": ["exclusion 1", "exclusion 2"],
  "probability_percent": 25,
  "confidence": "low|medium|high",
  "tags": ["ukraine", "russia", "2026"],
  "notes": "brief extraction notes"
}

Rules:
- The screenshot may be in Ukrainian, English, or mixed text.
- Preserve the forecast question meaning exactly.
- event_id must be ASCII snake_case and should include the year when possible.
- forecast_horizon must be the final resolution date, not the submission deadline.
- probability_percent must be an integer 0-100 only if an actual current probability is visible. If not visible, set it to null.
- If probability_percent is null, set confidence to low.
- If the image is not a forecast-event screenshot, set is_forecast_event=false and explain in notes.
- Do not invent missing dates or probabilities. Use null for missing values.
"""


class IngestError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_home(home: str | None) -> Path:
    if home:
        return Path(home).expanduser().resolve()
    if os.getenv("PROROK_HOME"):
        return Path(os.environ["PROROK_HOME"]).expanduser().resolve()
    return Path(DEFAULT_HOME).resolve()


def resolve_db(home: Path, db: str | None) -> Path:
    if db:
        return Path(db).expanduser().resolve()
    if os.getenv("PROROK_DB"):
        return Path(os.environ["PROROK_DB"]).expanduser().resolve()
    return home / "prorok.sqlite3"


def resolve_code_dir(code_dir: str | None) -> Path:
    candidates: list[Path] = []
    if code_dir:
        candidates.append(Path(code_dir).expanduser().resolve())
    if os.getenv("PROROK_CODE_DIR"):
        candidates.append(Path(os.environ["PROROK_CODE_DIR"]).expanduser().resolve())
    candidates.extend([Path(__file__).resolve().parent, Path("/app/prorok")])
    for candidate in candidates:
        if (candidate / "prorok_event_cli.py").exists() and (candidate / "prorok_assessment_cli.py").exists():
            return candidate
    raise IngestError("PROROK code directory not found")


def read_image_data_url(image_path: Path) -> str:
    if not image_path.exists():
        raise IngestError(f"image not found: {image_path}")
    if not image_path.is_file():
        raise IngestError(f"image path is not a file: {image_path}")
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def extract_json_block(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise IngestError(f"model did not return JSON: {cleaned[:500]}")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise IngestError("model JSON response is not an object")
    return parsed


def call_openai_vision(image_path: Path, model: str) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise IngestError(f"openai package is not available: {exc}") from exc

    data_url = read_image_data_url(image_path)
    client = OpenAI()

    if hasattr(client, "responses"):
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": EXTRACTION_PROMPT},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            max_output_tokens=2200,
        )
        text = getattr(response, "output_text", "") or ""
        return extract_json_block(text)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        max_tokens=2200,
    )
    text = response.choices[0].message.content or ""
    return extract_json_block(text)


def slug_ok(event_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", event_id or ""))


def probability_scale(probability: int) -> tuple[str, str]:
    if probability <= 5:
        return "0-5%", "Віддалена можливість"
    if probability <= 20:
        return "10-20%", "Низька ймовірність"
    if probability <= 35:
        return "25-35%", "Малоймовірно"
    if probability <= 50:
        return "40-50%", "Реалістична можливість"
    if probability <= 75:
        return "55-75%", "Ймовірно"
    if probability <= 90:
        return "80-90%", "Висока ймовірність"
    return "95-100%", "Майже напевно"


def normalize_tags(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip().lower() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [item.strip().lower() for item in value.split(",") if item.strip()]
    else:
        items = []
    if "prorok-screenshot" not in items:
        items.append("prorok-screenshot")
    return ",".join(dict.fromkeys(items))


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("is_forecast_event") is False:
        raise IngestError(f"image is not a forecast event: {payload.get('notes') or 'no details'}")

    required = ["event_id", "title", "question", "forecast_horizon"]
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise IngestError(f"missing required fields: {', '.join(missing)}")

    event_id = str(payload["event_id"]).strip()
    if not slug_ok(event_id):
        raise IngestError(f"invalid event_id, expected ASCII snake_case: {event_id}")

    probability = payload.get("probability_percent")
    if probability is not None:
        try:
            probability = int(probability)
        except Exception as exc:
            raise IngestError(f"probability_percent is not an integer: {payload.get('probability_percent')}") from exc
        if not 0 <= probability <= 100:
            raise IngestError(f"probability_percent out of range: {probability}")

    confidence = str(payload.get("confidence") or "medium").strip().lower()
    if confidence not in VALID_CONFIDENCE:
        confidence = "medium"
    if probability is None:
        confidence = "low"

    return {
        "event_id": event_id,
        "title": str(payload["title"]).strip(),
        "question": str(payload["question"]).strip(),
        "forecast_horizon": str(payload["forecast_horizon"]).strip(),
        "resolution_timezone": str(payload.get("resolution_timezone") or "").strip() or None,
        "forecast_submission_deadline": payload.get("forecast_submission_deadline"),
        "event_occurs_if": payload.get("event_occurs_if") if isinstance(payload.get("event_occurs_if"), list) else [],
        "does_not_count": payload.get("does_not_count") if isinstance(payload.get("does_not_count"), list) else [],
        "probability_percent": probability,
        "confidence": confidence,
        "tags": normalize_tags(payload.get("tags")),
        "notes": str(payload.get("notes") or "").strip(),
    }


def latest_probability(db_path: Path, event_id: str) -> int | None:
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        row = conn.execute(
            """
            SELECT probability_percent
            FROM assessments
            WHERE event_id = ?
            ORDER BY assessed_at DESC, assessment_id DESC
            LIMIT 1
            """,
            (event_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else int(row[0])


def event_exists(db_path: Path, event_id: str) -> bool:
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        row = conn.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    return row is not None


def run_checked(cmd: Sequence[str]) -> str:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise IngestError(
            "command failed:\n"
            + " ".join(cmd)
            + "\nSTDOUT:\n"
            + proc.stdout
            + "\nSTDERR:\n"
            + proc.stderr
        )
    return proc.stdout.strip()


def criteria_json(data: dict[str, Any]) -> str:
    payload = {
        "resolution_timezone": data["resolution_timezone"],
        "forecast_submission_deadline": data["forecast_submission_deadline"],
        "event_occurs_if": data["event_occurs_if"],
        "does_not_count": data["does_not_count"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def apply_event(
    *,
    code_dir: Path,
    db_path: Path,
    home: Path,
    data: dict[str, Any],
    image_path: Path,
    force_assessment: bool,
) -> dict[str, Any]:
    probability = data["probability_percent"]
    status = "active" if probability is not None else "paused"
    existed_before = event_exists(db_path, data["event_id"])

    event_out = run_checked(
        [
            sys.executable,
            str(code_dir / "prorok_event_cli.py"),
            "--home",
            str(home),
            "--db",
            str(db_path),
            "add-event",
            "--event-id",
            data["event_id"],
            "--title",
            data["title"],
            "--question",
            data["question"],
            "--status",
            status,
            "--forecast-horizon",
            data["forecast_horizon"],
            "--criteria-json",
            criteria_json(data),
            "--tags",
            data["tags"],
            "--source-image-note",
            f"Created from Telegram screenshot: {image_path.name}",
        ]
    )

    assessment_out = ""
    assessment_action = "skipped_no_probability"
    if probability is not None:
        previous = latest_probability(db_path, data["event_id"])
        if force_assessment or previous != probability:
            band, label = probability_scale(probability)
            assessment_out = run_checked(
                [
                    sys.executable,
                    str(code_dir / "prorok_assessment_cli.py"),
                    "--home",
                    str(home),
                    "--db",
                    str(db_path),
                    "add-assessment",
                    data["event_id"],
                    "--probability",
                    str(probability),
                    "--band",
                    band,
                    "--label",
                    label,
                    "--confidence",
                    data["confidence"],
                    "--rationale",
                    "Initial baseline assessment extracted from PROROK Telegram screenshot.",
                ]
            )
            assessment_action = "added"
        else:
            assessment_action = "skipped_same_latest_probability"

    return {
        "event_id": data["event_id"],
        "created_new": not existed_before,
        "status": status,
        "probability_percent": probability,
        "assessment_action": assessment_action,
        "event_output": event_out,
        "assessment_output": assessment_out,
    }


def append_audit(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest PROROK forecast event from screenshot")
    parser.add_argument("--image", required=True, help="Local image path supplied by OpenClaw attachment context")
    parser.add_argument("--home", default=os.getenv("PROROK_HOME", DEFAULT_HOME))
    parser.add_argument("--db", help="SQLite DB path")
    parser.add_argument("--code-dir", help="PROROK code directory")
    parser.add_argument("--model", default=os.getenv("PROROK_SCREENSHOT_MODEL", DEFAULT_MODEL))
    parser.add_argument("--audit-log", default=os.getenv("PROROK_SCREENSHOT_AUDIT_LOG", DEFAULT_AUDIT_LOG))
    parser.add_argument("--dry-run", action="store_true", help="Extract and validate only; do not write DB")
    parser.add_argument("--force-assessment", action="store_true", help="Append assessment even when latest probability is unchanged")
    parser.add_argument("--json-output", action="store_true", help="Print machine-readable JSON")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    image_path = Path(args.image).expanduser().resolve()
    home = resolve_home(args.home)
    db_path = resolve_db(home, args.db)
    code_dir = resolve_code_dir(args.code_dir)
    audit_log = Path(args.audit_log).expanduser().resolve()

    record: dict[str, Any] = {
        "timestamp_utc": utc_now(),
        "action": "prorok_screenshot_ingest",
        "image": str(image_path),
        "model": args.model,
        "dry_run": bool(args.dry_run),
        "result": "started",
    }

    try:
        raw = call_openai_vision(image_path, args.model)
        data = validate_payload(raw)
        record["extracted"] = data

        if args.dry_run:
            result = {"dry_run": True, "event": data}
        else:
            result = apply_event(
                code_dir=code_dir,
                db_path=db_path,
                home=home,
                data=data,
                image_path=image_path,
                force_assessment=args.force_assessment,
            )

        record["result"] = "ok"
        record["apply_result"] = result
        append_audit(audit_log, record)

        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("PROROK_SCREENSHOT_INGEST")
            print("status: ok")
            print(f"event_id: {data['event_id']}")
            print(f"title: {data['title']}")
            print(f"event_status: {result.get('status', 'dry_run')}")
            print(f"probability: {data['probability_percent'] if data['probability_percent'] is not None else 'n/a'}")
            print(f"assessment_action: {result.get('assessment_action', 'dry_run')}")
            print(f"audit_log: {audit_log}")
        return 0

    except Exception as exc:
        record["result"] = "error"
        record["error"] = str(exc)
        append_audit(audit_log, record)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
