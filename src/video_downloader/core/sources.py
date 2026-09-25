from __future__ import annotations

import json
import logging
import platform
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

USER_AGENT = "VideoDownloader/1.0 (+https://github.com/Th3-A6add0n/video-downloader)"


@dataclass
class ResolvedBinary:
    name: str
    version: str
    url: str
    archive: str                 # "none" | "zip" | "tar.xz" | "tar.gz"
    executables: list[str]
    sha256: str = ""             # empty -> skip verification (log a warning)
    strip_components: int = 0
    source: str = ""             # for diagnostics: "github:yt-dlp", "btbn", "evermeet"


class Resolver(Protocol):
    name: str

    def resolve(self) -> ResolvedBinary: ...


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

def _http_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _norm_arch(machine: str) -> str:
    m = machine.lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(m, m)


def _system() -> str:
    return platform.system().lower()


# --------------------------------------------------------------------------- #
# yt-dlp  (GitHub Releases)
# --------------------------------------------------------------------------- #

class YtDlpResolver:
    """
    Resolves the latest yt-dlp release from GitHub.

    Uses the stable channel by default. To track nightlies, swap REPO to
    "yt-dlp/yt-dlp-nightly-builds" — the asset names are the same.
    """
    name = "yt-dlp"
    REPO = "yt-dlp/yt-dlp"

    def resolve(self) -> ResolvedBinary:
        release = _http_json(f"https://api.github.com/repos/{self.REPO}/releases/latest")
        version = release["tag_name"]
        assets = {a["name"]: a for a in release.get("assets", [])}

        asset_name = self._asset_name()
        if asset_name not in assets:
            raise RuntimeError(
                f"yt-dlp release {version} has no asset for this platform "
                f"(expected {asset_name})."
            )
        asset = assets[asset_name]

        sha256 = ""
        if "SHA2-256SUMS" in assets:
            sha256 = self._fetch_sha(assets["SHA2-256SUMS"]["browser_download_url"], asset_name)

        return ResolvedBinary(
            name=self.name,
            version=version,
            url=asset["browser_download_url"],
            archive="none",
            executables=[asset_name],
            sha256=sha256,
            source=f"github:{self.REPO}",
        )

    def _asset_name(self) -> str:
        system = _system()
        arch = _norm_arch(platform.machine())
        if system == "windows":
            return "yt-dlp.exe"
        if system == "darwin":
            return "yt-dlp_macos"
        if system == "linux":
            return {
                "x86_64": "yt-dlp_linux",
                "arm64": "yt-dlp_linux_aarch64",
                "armv7l": "yt-dlp_linux_armv7l",
            }.get(arch, "yt-dlp_linux")
        raise RuntimeError(f"Unsupported platform for yt-dlp: {system}/{arch}")

    @staticmethod
    def _fetch_sha(sums_url: str, target: str) -> str:
        try:
            text = _http_text(sums_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch yt-dlp checksums: %s", exc)
            return ""
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == target:
                return parts[0].lower()
        return ""


# --------------------------------------------------------------------------- #
# FFmpeg — Windows / Linux  (BtbN/FFmpeg-Builds)
# --------------------------------------------------------------------------- #

class BtbNFfmpegResolver:
    """
    Resolves the latest BtbN FFmpeg static build.

    BtbN publishes a rolling `latest` tag plus a `checksums.sha256` asset
    that covers every archive in the release.
    """
    name = "ffmpeg"
    REPO = "BtbN/FFmpeg-Builds"
    BASE = f"https://github.com/{REPO}/releases/download/latest"

    def resolve(self) -> ResolvedBinary:
        try:
            release = _http_json(f"https://api.github.com/repos/{self.REPO}/releases/latest")
            version = release.get("tag_name", "latest")
        except Exception:  # noqa: BLE001
            version = "latest"

        asset_name = self._asset_name()
        url = f"{self.BASE}/{asset_name}"

        sha256 = ""
        try:
            text = _http_text(f"{self.BASE}/checksums.sha256")
            for line in text.splitlines():
                parts = line.strip().split()
                if len(parts) == 2 and parts[1] == asset_name:
                    sha256 = parts[0].lower()
                    break
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch BtbN checksums: %s", exc)

        return ResolvedBinary(
            name=self.name,
            version=version,
            url=url,
            archive="zip" if asset_name.endswith(".zip") else "tar.xz",
            executables=["ffmpeg.exe", "ffprobe.exe"] if _system() == "windows"
                        else ["ffmpeg", "ffprobe"],
            sha256=sha256,
            source=f"github:{self.REPO}",
        )

    def _asset_name(self) -> str:
        system = _system()
        arch = _norm_arch(platform.machine())

        if system == "windows":
            return "ffmpeg-master-latest-win64-gpl.zip"

        if system == "linux":
            if arch == "arm64":
                return "ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
            return "ffmpeg-master-latest-linux64-gpl.tar.xz"

        raise RuntimeError(f"BtbN resolver does not support {system}")


# --------------------------------------------------------------------------- #
# FFmpeg — macOS  (evermeet.cx)
# --------------------------------------------------------------------------- #

class EvermeetFfmpegResolver:
    """
    Resolves the latest macOS ffmpeg + ffprobe from evermeet.cx.

    evermeet ships ffmpeg and ffprobe as separate archives, so the manager
    installs them into the same versioned directory using two downloads.
    """
    name = "ffmpeg"
    INFO = "https://evermeet.cx/ffmpeg/info"

    def resolve(self) -> ResolvedBinary:
        data = _http_json(f"{self.INFO}/ffmpeg/snapshot")
        version = data["version"]
        url = data["download"]["zip"]["url"]

        return ResolvedBinary(
            name=self.name,
            version=version,
            url=url,
            archive="zip",
            executables=["ffmpeg", "ffprobe"],
            sha256="",
            source="evermeet",
        )

    def resolve_ffprobe(self) -> ResolvedBinary:
        data = _http_json(f"{self.INFO}/ffprobe/snapshot")
        return ResolvedBinary(
            name="ffprobe",
            version=data["version"],
            url=data["download"]["zip"]["url"],
            archive="zip",
            executables=["ffprobe"],
            sha256="",
            source="evermeet",
        )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def default_resolvers() -> dict[str, Resolver]:
    """Returns the resolver for each logical binary name on this platform."""
    system = _system()
    if system == "darwin":
        return {"ffmpeg": EvermeetFfmpegResolver(), "yt-dlp": YtDlpResolver()}
    return {"ffmpeg": BtbNFfmpegResolver(), "yt-dlp": YtDlpResolver()}
