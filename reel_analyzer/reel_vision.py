"""OpenClaw native vision adapter for Reel contact sheets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

VISION_PROMPT = """This is a 3x2 contact sheet of six chronological frames from one Instagram Reel. Analyze every panel as part of the same video. Extract only useful informational content: readable text, URLs, named tools/resources, frameworks, templates, and practical advice. Do not describe the person's appearance. Preserve exact URLs and English template wording only when reliably readable. Return concise factual Ukrainian. Do not invent unreadable details."""


def describe_contact_sheet(image: Path, *, model: str = "openai/gpt-4.1-mini") -> str:
    proc = subprocess.run(
        ["openclaw", "infer", "image", "describe", "--file", str(image), "--model", model, "--prompt", VISION_PROMPT, "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Vision inference failed: {(proc.stderr or proc.stdout).strip()[-1200:]}")
    try:
        payload = json.loads(proc.stdout)
        outputs = payload.get("outputs") or []
        text = str(outputs[0].get("text") or "").strip()
    except (json.JSONDecodeError, IndexError, AttributeError) as exc:
        raise RuntimeError("Vision inference returned invalid JSON.") from exc
    if not text:
        raise RuntimeError("Vision inference returned no text.")
    return text
