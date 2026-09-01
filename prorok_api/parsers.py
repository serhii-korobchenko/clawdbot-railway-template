from __future__ import annotations

import json
from typing import Any


def parse_decision_criteria(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {"format": "text", "data": None, "raw": ""}

    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"format": "text", "data": None, "raw": raw}

    if isinstance(parsed, dict):
        return {"format": "structured", "data": parsed, "raw": raw}

    return {"format": "text", "data": None, "raw": raw}


def parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []

    if not isinstance(parsed, list):
        return []

    return [item for item in parsed if isinstance(item, str)]
