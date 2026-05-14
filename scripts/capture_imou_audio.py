#!/usr/bin/env python3
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SKILL_SCRIPT = "/data/workspace/skills/imou-device-video/scripts/device_video.py"


def run_command(cmd: list[str]) -> str:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed:\n{' '.join(cmd)}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )

    return result.stdout.strip()


def get_hls_url(skill_script: str, device_id: str, channel_id: str, stream_id: str) -> str:
    output = run_command([
        "python3",
        skill_script,
        "live",
        device_id,
        channel_id,
        "--stream-id",
        stream_id,
    ])

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    hls_candidates = [line for line in lines if ".m3u8" in line]

    if not hls_candidates:
        raise RuntimeError(f"No HLS URL found in skill output:\n{output}")

    return hls_candidates[-1]


def capture_audio(hls_url: str, duration: int, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        hls_url,
        "-t",
        str(duration),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_file),
    ]

    run_command(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture audio from IMOU cloud HLS stream.")
    parser.add_argument("--device-id", required=True, help="IMOU device serial number")
    parser.add_argument("--channel-id", default="0", help="IMOU channel ID, usually 0")
    parser.add_argument("--stream-id", default="0", help="0 = main stream, 1 = sub stream")
    parser.add_argument("--duration", type=int, default=10, help="Capture duration in seconds")
    parser.add_argument("--out-dir", default="/data/workspace/imou_audio", help="Output directory")
    parser.add_argument("--skill-script", default=DEFAULT_SKILL_SCRIPT, help="Path to device_video.py")

    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.out_dir)
    output_file = output_dir / f"imou_{args.device_id}_{timestamp}_{args.duration}s.wav"
    metadata_file = output_file.with_suffix(".json")

    hls_url = get_hls_url(
        skill_script=args.skill_script,
        device_id=args.device_id,
        channel_id=args.channel_id,
        stream_id=args.stream_id,
    )

    capture_audio(
        hls_url=hls_url,
        duration=args.duration,
        output_file=output_file,
    )

    metadata = {
        "device_id": args.device_id,
        "channel_id": args.channel_id,
        "stream_id": args.stream_id,
        "duration_seconds": args.duration,
        "created_at_utc": timestamp,
        "audio_file": str(output_file),
        "hls_url": hls_url,
    }

    metadata_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "audio_file": str(output_file),
        "metadata_file": str(metadata_file),
        "duration_seconds": args.duration,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
