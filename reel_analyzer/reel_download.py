"""Validated public Instagram Reel download helpers."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

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

_POST_PATH = re.compile(r"^/p/([A-Za-z0-9_-]+)/?$")
_PUBLIC_HOSTS = _ALLOWED_HOSTS | {"oginstagram.com", "www.oginstagram.com"}


def canonicalize_instagram_url(raw_url: str) -> tuple[str, str, int | None]:
    parts = urlsplit((raw_url or "").strip())
    if parts.scheme != "https" or (parts.hostname or "").lower() not in _PUBLIC_HOSTS:
        raise InvalidReelUrl("Only public Instagram HTTPS URLs are supported.")
    reel = _REEL_PATH.fullmatch(parts.path)
    post = _POST_PATH.fullmatch(parts.path)
    if not reel and not post:
        raise InvalidReelUrl("Expected an Instagram Reel or post URL.")
    kind, match = ("reel", reel) if reel else ("p", post)
    raw_index = parse_qs(parts.query).get("img_index", [None])[0] if kind == "p" else None
    if raw_index is not None and (not re.fullmatch(r"[1-9][0-9]*", raw_index) or int(raw_index) > 20):
        raise InvalidReelUrl("Invalid img_index.")
    selected = int(raw_index) if raw_index else None
    url = f"https://www.instagram.com/{kind}/{match.group(1)}/"
    if selected is not None:
        url += f"?img_index={selected}"
    return url, kind, selected
