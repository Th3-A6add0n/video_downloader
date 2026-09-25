from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareCapabilities:
    """Result of probing ffmpeg for usable hardware acceleration."""
    encoder: str | None          # e.g. "h264_nvenc" or None for CPU
    decoder: str | None          # e.g. "cuda" or None for CPU decode
    label: str                   # human-readable label for the UI

    @property
    def is_hardware(self) -> bool:
        return self.encoder is not None


CPU_FALLBACK = HardwareCapabilities(None, None, "CPU (libx264)")


# Per-encoder ffmpeg arguments.
#
# Quality targets are tuned to approximately match x264 CRF 23, which is
# x264's own default and the point of diminishing returns for most content.
# Going lower (e.g. CRF 20 / cq 23) produces files roughly 40-60% larger
# with no visible quality gain on typical sources.
#
# Tuple is (decoder, preset_args):
#   - decoder: value for -hwaccel / -hwaccel_output_format, or None.
#   - preset_args: encoder-specific quality and speed flags.
_ENCODER_TABLE: dict[str, tuple[str | None, list[str]]] = {
    "h264_nvenc": (
        None,
        ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "28", "-b:v", "0"],
    ),
    "h264_qsv": (
        None,
        ["-preset", "medium", "-global_quality", "26", "-look_ahead", "1"],
    ),
    "h264_amf": (
        None,
        ["-quality", "balanced", "-rc", "cqp", "-qp_i", "26", "-qp_p", "26"],
    ),
    "h264_videotoolbox": (
        None,
        ["-q:v", "50"],
    ),
    "h264_vaapi": (
        "vaapi",
        ["-qp", "26"],
    ),
    "libx264": (
        None,
        ["-preset", "slow", "-crf", "23", "-profile:v", "high", "-pix_fmt", "yuv420p"],
    ),
}


# Preference order per OS. First entry that passes the smoke test wins.
_PLATFORM_PREFERENCE: dict[str, list[tuple[str, str]]] = {
    "windows": [
        ("h264_nvenc", "NVIDIA NVENC"),
        ("h264_qsv", "Intel Quick Sync"),
        ("h264_amf", "AMD AMF"),
    ],
    "darwin": [
        ("h264_videotoolbox", "Apple VideoToolbox"),
    ],
    "linux": [
        ("h264_nvenc", "NVIDIA NVENC"),
        ("h264_qsv", "Intel Quick Sync"),
        ("h264_vaapi", "VAAPI"),
    ],
}


def detect(ffmpeg: Path) -> HardwareCapabilities:
    """
    Probe `ffmpeg` for hardware encoders, smoke-test each candidate, and
    return the best one available. Falls back to CPU if none work.
    """
    try:
        encoder_list = _run(ffmpeg, ["-hide_banner", "-encoders"])
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not query ffmpeg encoders: %s", exc)
        return CPU_FALLBACK

    system = platform.system().lower()
    candidates = _PLATFORM_PREFERENCE.get(system, [])

    for encoder, label in candidates:
        if encoder not in encoder_list:
            log.debug("Encoder %s not built into this ffmpeg", encoder)
            continue
        if _smoke_test(ffmpeg, encoder):
            decoder = _ENCODER_TABLE.get(encoder, (None, []))[0]
            log.info("Selected hardware encoder: %s (%s)", encoder, label)
            return HardwareCapabilities(encoder=encoder, decoder=decoder, label=label)
        log.warning("Encoder %s present but smoke test failed; trying next", encoder)

    log.info("No usable hardware encoder; using CPU (libx264).")
    return CPU_FALLBACK


def encoder_args(caps: HardwareCapabilities) -> list[str]:
    """
    Return the ffmpeg -c:v ... argument block for the given capabilities.
    Always includes the encoder itself; adds preset/quality flags from the table.
    """
    encoder = caps.encoder or "libx264"
    _, preset_args = _ENCODER_TABLE.get(encoder, _ENCODER_TABLE["libx264"])
    return ["-c:v", encoder, *preset_args]


def decoder_args(caps: HardwareCapabilities) -> list[str]:
    """
    Return -hwaccel / -hwaccel_output_format arguments for optional GPU decode,
    or an empty list to decode on the CPU.
    """
    if not caps.decoder:
        return []
    return [
        "-hwaccel", caps.decoder,
        "-hwaccel_output_format", caps.decoder,
    ]


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

def _run(ffmpeg: Path, args: list[str]) -> str:
    result = subprocess.run(
        [str(ffmpeg), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout + result.stderr


def _smoke_test(ffmpeg: Path, encoder: str) -> bool:
    """Encode one synthetic frame to verify the encoder and drivers actually work."""
    try:
        result = subprocess.run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel", "error",
                "-f", "lavfi",
                "-i", "color=c=black:s=320x240:d=0.1",
                "-frames:v", "1",
                "-c:v", encoder,
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Smoke test error for %s: %s", encoder, exc)
        return False
    if result.returncode != 0:
        log.debug("Smoke test failed for %s: %s", encoder, result.stderr.strip()[:200])
    return result.returncode == 0
