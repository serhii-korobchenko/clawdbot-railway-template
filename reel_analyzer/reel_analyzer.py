"""End-to-end public Instagram Reel extraction for downstream agent synthesis."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from openai import OpenAI

from .reel_download import canonicalize_reel_url, download_public_reel
from .reel_media import extract_audio, extract_contact_sheet
from .reel_vision import describe_contact_sheet


class ReelAnalysisError(RuntimeError):
    pass


class ReelAnalyzer:
    def __init__(self, *, max_duration: int = 300, vision_model: str = "openai/gpt-4.1-mini"):
        self.max_duration = max_duration
        self.vision_model = vision_model

    def _transcribe(self, audio: Path) -> str:
        if not os.getenv("OPENAI_API_KEY"):
            raise ReelAnalysisError("OPENAI_API_KEY is not configured.")
        client = OpenAI()
        with audio.open("rb") as handle:
            result = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=handle)
        return str(result.text or "").strip()

    def analyze(self, raw_url: str) -> dict:
        url = canonicalize_reel_url(raw_url)
        try:
            with tempfile.TemporaryDirectory(prefix="reel-analyzer-") as tmp:
                workdir = Path(tmp)
                media, metadata = download_public_reel(url, workdir, max_duration=self.max_duration)
                audio = extract_audio(media, workdir / "audio.wav")
                sheet = extract_contact_sheet(media, workdir / "contact-sheet.jpg")
                transcript = self._transcribe(audio)
                visual = describe_contact_sheet(sheet, model=self.vision_model)
                return {
                    "url": url,
                    "title": metadata.get("title"),
                    "uploader": metadata.get("uploader"),
                    "duration": metadata.get("duration"),
                    "transcript": transcript,
                    "visual_facts": visual,
                }
        except ReelAnalysisError:
            raise
        except Exception as exc:
            raise ReelAnalysisError(str(exc)) from exc


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Analyze a public Instagram Reel for OpenClaw.")
    parser.add_argument("url")
    args = parser.parse_args()
    try:
        print(json.dumps(ReelAnalyzer().analyze(args.url), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
