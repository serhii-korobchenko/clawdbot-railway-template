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


def describe_post_images(images: list[Path], *, selected: int | None = None, kinds: list[str] | None = None, model: str = "openai/gpt-4.1-mini") -> str:
    """Analyze each carousel slide independently, preserving its index."""
    if not images:
        raise ValueError("No carousel images.")
    if kinds is not None and (len(kinds) != len(images) or any(k not in ("image", "video") for k in kinds)):
        raise ValueError("Invalid carousel media types.")
    output = []
    for index, path in enumerate(images, 1):
        video = kinds is not None and kinds[index - 1] == "video"
        focus = " This is the user-selected slide." if index == selected else ""
        prompt = (
            f"Instagram carousel slide {index} of {len(images)}. "
            + ("This is one representative frame of a video, not its full content or audio. " if video else "This is a photo. ")
            + focus + " "
            "Extract readable text, resources, URLs and practical information in Ukrainian. "
            "Preserve reliably legible exact wording. Do not invent unreadable details or describe appearance."
        )
        proc = subprocess.run(
            ["openclaw", "infer", "image", "describe", "--file", str(path),
             "--model", model, "--prompt", prompt, "--json"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )
        if proc.returncode:
            raise RuntimeError(f"Post vision inference failed on slide {index}: {(proc.stderr or proc.stdout).strip()[-500:]}")
        try:
            payload = json.loads(proc.stdout)
            detail = str((payload.get("outputs") or [])[0].get("text") or "").strip()
        except (ValueError, IndexError, AttributeError) as exc:
            raise RuntimeError(f"Invalid vision result for slide {index}.") from exc
        if not detail:
            raise RuntimeError(f"Empty vision result for slide {index}.")
        output.append(f"Слайд {index}" + (" (відео: один кадр)" if video else " (фото)") + (" (обраний)" if index == selected else "") + f":\n{detail}")
    return "\n\n".join(output)
