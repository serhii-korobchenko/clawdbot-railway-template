"""ffmpeg-based audio and representative-frame extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-1200:]
        raise RuntimeError(f"Media command failed: {detail}")


def _probe_duration(media: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-1200:]
        raise RuntimeError(f"Could not determine Reel duration: {detail}")
    try:
        duration = float(proc.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Could not determine Reel duration.") from exc
    if duration <= 0:
        raise RuntimeError("Reel duration must be positive.")
    return duration


def _representative_timestamps(duration: float, frames: int = 6) -> list[float]:
    if duration <= 0:
        raise ValueError("Duration must be positive.")
    if frames <= 0:
        raise ValueError("Frame count must be positive.")
    # Sample the midpoint of each equal segment, avoiding intro/end boundaries.
    return [duration * (index + 0.5) / frames for index in range(frames)]


def extract_audio(media: Path, output: Path) -> Path:
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(output)])
    return output


def extract_contact_sheet(media: Path, output: Path, *, frames: int = 6) -> Path:
    if frames != 6:
        raise ValueError("MVP contact sheet currently supports exactly 6 frames.")

    duration = _probe_duration(media)
    frame_paths = [output.parent / f"frame-{i:02d}.jpg" for i in range(1, 7)]
    for timestamp, frame_path in zip(_representative_timestamps(duration, frames), frame_paths):
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(media),
                "-frames:v",
                "1",
                "-vf",
                "scale=540:-1",
                str(frame_path),
            ]
        )

    if not all(p.exists() for p in frame_paths):
        raise RuntimeError("Could not extract six representative Reel frames.")

    inputs: list[str] = []
    for p in frame_paths:
        inputs.extend(["-i", str(p)])
    graph = (
        "[0:v][1:v][2:v]hstack=inputs=3[top];"
        "[3:v][4:v][5:v]hstack=inputs=3[bottom];"
        "[top][bottom]vstack=inputs=2[out]"
    )
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", str(output)])
    return output
