from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class MediaInfo:
    container: str
    video_codec: str
    video_bitrate: int
    width: int
    height: int
    audio_codec: str
    audio_bitrate: int
    duration: float


def ffprobe_path(ffmpeg: Path) -> Path:
    """Derive the ffprobe path from the ffmpeg path (sibling binary)."""
    name = "ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"
    return ffmpeg.with_name(name)


def probe(ffmpeg: Path, media: Path, timeout: float = 30.0) -> MediaInfo | None:
    """
    Inspect `media` with ffprobe. Returns None on any failure so callers can
    fall back to a safe default (transcode).
    """
    probe_bin = ffprobe_path(ffmpeg)
    if not probe_bin.exists():
        log.warning("ffprobe not found at %s", probe_bin)
        return None

    args = [
        str(probe_bin),
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(media),
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("ffprobe failed: %s", exc)
        return None

    if result.returncode != 0:
        log.warning("ffprobe exit %d: %s", result.returncode, result.stderr.strip()[:200])
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        return None

    return MediaInfo(
        container=fmt.get("format_name", ""),
        video_codec=(video.get("codec_name") or "").lower(),
        video_bitrate=int(video.get("bit_rate") or 0),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        audio_codec=(audio.get("codec_name") or "").lower() if audio else "",
        audio_bitrate=int(audio.get("bit_rate") or 0) if audio else 0,
        duration=float(fmt.get("duration") or 0),
    )