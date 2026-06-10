#!/usr/bin/env python3
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import requests
from openai import OpenAI


DEVICE_ID = os.getenv("IMOU_DEVICE_ID", "A683BBHPSFD935E")
CHANNEL_ID = os.getenv("IMOU_CHANNEL_ID", "0")
STREAM_ID = os.getenv("IMOU_STREAM_ID", "0")
DURATION = int(os.getenv("IMOU_AUDIO_DURATION", "10"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_TARGET_CHAT_ID = (
    os.getenv("IMOU_TELEGRAM_TARGET_CHAT_ID")
    or os.getenv("TELEGRAM_TARGET_CHAT_ID")
)

SKILL_SCRIPT = "/data/workspace/skills/imou-device-video/scripts/device_video.py"
OUT_DIR = Path("/data/workspace/imou_audio")


def run(cmd: list[str]) -> str:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDERR:\n{result.stderr}")
    return result.stdout.strip()


def get_hls_url() -> str:
    output = run([
        "python3",
        SKILL_SCRIPT,
        "live",
        DEVICE_ID,
        CHANNEL_ID,
        "--stream-id",
        STREAM_ID,
    ])

    for line in output.splitlines():
        if ".m3u8" in line:
            return line.strip()

    raise RuntimeError(f"No HLS URL found in output:\n{output}")


def capture_audio(hls_url: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audio_path = OUT_DIR / f"imou_{DEVICE_ID}_{ts}_{DURATION}s.wav"

    run([
        "ffmpeg",
        "-y",
        "-i",
        hls_url,
        "-t",
        str(DURATION),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ])

    return audio_path


def transcribe(audio_path: Path) -> str:
    client = OpenAI()

    with audio_path.open("rb") as f:
        result = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=f,
            language="uk",
        )

    return result.text.strip()


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_TARGET_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or IMOU_TELEGRAM_TARGET_CHAT_ID/TELEGRAM_TARGET_CHAT_ID is missing")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_TARGET_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def main() -> None:
    hls_url = get_hls_url()
    audio_path = capture_audio(hls_url)
    transcript = transcribe(audio_path)

    message = (
        "🎙 <b>IMOU audio transcription</b>\n\n"
        f"<b>Device:</b> {DEVICE_ID}\n"
        f"<b>Duration:</b> {DURATION}s\n\n"
        f"{transcript if transcript else 'Текст не розпізнано.'}"
    )

    send_telegram(message)

    print(json.dumps({
        "status": "ok",
        "audio_file": str(audio_path),
        "transcript": transcript,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
