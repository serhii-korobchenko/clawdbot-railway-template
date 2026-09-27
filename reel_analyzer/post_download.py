"""Public Instagram embed carousel extraction, without login or cookies."""
from __future__ import annotations

import json
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .reel_download import canonicalize_instagram_url


class PostExtractionError(RuntimeError):
    pass


def extract_post_media(raw_url: str, *, session=None) -> tuple[list[dict], int | None]:
    url, kind, selected = canonicalize_instagram_url(raw_url)
    if kind != "p":
        raise PostExtractionError("Expected an Instagram post URL.")
    http = session or requests.Session()
    response = http.get(url.split("?")[0] + "embed/captioned/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    response.raise_for_status()
    page = unescape(response.text)
    marker = 'edge_sidecar_to_children\\":'
    position = page.find(marker)
    if position < 0:
        # Single-image embeds expose display_url on the parent media object.
        # Keep this deliberately separate from the carousel node parser.
        match = __import__("re").search(r'display_url\\\\":\\\\"(https?:.*?)(?<!\\\\)\\\\"', page)
        if not match:
            raise PostExtractionError("Public post media metadata is unavailable.")
        image_url = match.group(1)
        for _ in range(3):
            image_url = image_url.replace("\\\\/", "/").replace("\\/", "/")
        parts = urlsplit(image_url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443) or not (host.endswith(".cdninstagram.com") or host.endswith(".fbcdn.net")):
            raise PostExtractionError("Invalid post image URL.")
        if selected not in (None, 1):
            raise PostExtractionError("Selected img_index exceeds post length.")
        return [{"kind": "image", "url": image_url}], selected
    # Embed stores a JSON object inside an escaped string. Decode only the
    # sidecar object, not the whole page or unrelated captions/scripts.
    fragment = page[position + len(marker):]
    fragment = fragment.replace('\\\"', '"')
    try:
        sidecar, _ = json.JSONDecoder().raw_decode(fragment)
        nodes = [entry["node"] for entry in sidecar["edges"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise PostExtractionError("Invalid public carousel metadata.") from exc
    if not nodes or len(nodes) > 20:
        raise PostExtractionError("Unsupported carousel size.")
    media = []
    for node in nodes:
        is_video = bool(node.get("is_video"))
        image_url = node.get("video_url") if is_video else node.get("display_url")
        if not isinstance(image_url, str):
            raise PostExtractionError("Missing carousel media URL.")
        # Instagram embeds can double-escape forward slashes in nested JSON.
        for _ in range(3):
            image_url = image_url.replace("\\\\/", "/").replace("\\/", "/")
        parts = urlsplit(image_url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443) or not (host.endswith(".cdninstagram.com") or host.endswith(".fbcdn.net")):
            raise PostExtractionError("Invalid carousel image URL.")
        media.append({"kind": "video" if is_video else "image", "url": image_url})
    if selected is not None and selected > len(media):
        raise PostExtractionError("Selected img_index exceeds carousel length.")
    return media, selected


def extract_post_images(raw_url: str, *, session=None) -> tuple[list[str], int | None]:
    """Legacy image-only interface: never misrepresent video slides as images."""
    media, selected = extract_post_media(raw_url, session=session)
    if any(item["kind"] != "image" for item in media):
        raise PostExtractionError("Mixed/video carousels require video-aware processing.")
    return [item["url"] for item in media], selected


def download_post_images(raw_url: str, workdir: Path, *, session=None) -> tuple[list[Path], int | None]:
    http = session or requests.Session()
    urls, selected = extract_post_images(raw_url, session=http)
    workdir.mkdir(parents=True, exist_ok=True)
    images = []
    for index, url in enumerate(urls, 1):
        response = http.get(url, timeout=25)
        response.raise_for_status()
        if not response.headers.get("Content-Type", "").lower().startswith("image/"):
            raise PostExtractionError("Carousel media is not an image.")
        if len(response.content) > 12 * 1024 * 1024:
            raise PostExtractionError("Carousel image exceeds size limit.")
        path = workdir / f"post-{index:02d}.jpg"
        path.write_bytes(response.content)
        images.append(path)
    return images, selected
