"""Validated public Instagram Reel download helpers."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from yt_dlp import YoutubeDL

_REEL_PATH = re.compile(r"^/reel/([A-Za-z0-9_-]+)/?$")
_ALLOWED_HOSTS = {"instagram.com", "www.instagram.com"}


class InvalidReelUrl(ValueError):
    pass


def canonicalize_reel_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in _ALLOWED_HOSTS:
        raise InvalidReelUrl("Only public https://www.instagram.com/reel/... URLs are supported.")
    match = _REEL_PATH.fullmatch(parts.path)
    if not match:
        raise InvalidReelUrl("URL must point to an Instagram Reel.")
    return urlunsplit(("https", "www.instagram.com", f"/reel/{match.group(1)}/", "", ""))


def download_public_reel(url: str, workdir: Path, *, max_duration: int = 300) -> tuple[Path, dict]:
    canonical = canonicalize_reel_url(url)
    workdir.mkdir(parents=True, exist_ok=True)
    output_template = str(workdir / "reel.%(ext)s")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "outtmpl": output_template,
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(canonical, download=False)
            duration = int(info.get("duration") or 0)
            if duration and duration > max_duration:
                raise ValueError(f"Reel is too long ({duration}s; limit {max_duration}s).")
            ydl.download([canonical])
    except Exception as exc:
        raise RuntimeError(f"Could not download public Reel: {exc}") from exc

    candidates = sorted(workdir.glob("reel.*"))
    media = next((p for p in candidates if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}), None)
    if media is None:
        raise RuntimeError("yt-dlp completed without a supported media file.")
    return media, info
