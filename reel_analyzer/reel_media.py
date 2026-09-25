"""ffmpeg-based audio and representative-frame extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-1200:]
        raise RuntimeError(f"Media command failed: {detail}")


def extract_audio(media: Path, output: Path) -> Path:
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(output)])
    return output


def extract_contact_sheet(media: Path, output: Path, *, frames: int = 6) -> Path:
    if frames != 6:
        raise ValueError("MVP contact sheet currently supports exactly 6 frames.")
    pattern = str(output.parent / "frame-%02d.jpg")
    # Six representative frames, avoiding dependence on exact Reel duration.
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-vf", "fps=6/(duration+0.001),scale=540:-1", "-frames:v", "6", pattern])
    frame_paths = [output.parent / f"frame-{i:02d}.jpg" for i in range(1, 7)]
    if not all(p.exists() for p in frame_paths):
        # Fallback for ffmpeg builds where duration is unavailable to the filter.
        for p in frame_paths:
            p.unlink(missing_ok=True)
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-vf", "thumbnail=60,scale=540:-1", "-frames:v", "6", pattern])
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
