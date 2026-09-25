from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from video_downloader import paths
from video_downloader.core.filename import sanitize_filename, unique_path
from video_downloader.core.hardware import (
    CPU_FALLBACK,
    HardwareCapabilities,
    decoder_args,
    encoder_args,
)
from video_downloader.core.probe import probe as probe_media

log = logging.getLogger(__name__)

# yt-dlp emits progress lines like:
#   DLP|<downloaded_bytes>|<total_bytes>|<total_bytes_estimate>
# where any field may be "NA" or "None".
_PROGRESS_PREFIX = "DLP|"
_DEST_RE = re.compile(r"\[download\] Destination:\s+(.+)$", re.MULTILINE)
_ERROR_RE = re.compile(r"ERROR:\s*(.+?)(?:\r?\n|$)", re.MULTILINE)
_FORMAT_SUFFIX_RE = re.compile(r"\.f\d+$")

_MAX_BUFFER = 256 * 1024
_STALL_MS = 60_000
_UI_THROTTLE_MS = 500
_SPEED_WINDOW_SECONDS = 5.0


VIDEO_FORMATS = {
    "best": "bv*+ba/b",
    "2160p": "bv*[height<=2160]+ba/b[height<=2160]",
    "1440p": "bv*[height<=1440]+ba/b[height<=1440]",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
    "720p": "bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480]+ba/b[height<=480]",
    "360p": "bv*[height<=360]+ba/b[height<=360]",
    "240p": "bv*[height<=240]+ba/b[height<=240]",
    "144p": "bv*[height<=144]+ba/b[height<=144]",
}


class DownloadJob(QObject):
    STATE_QUEUED = "Queued"
    STATE_DOWNLOADING = "Downloading"
    STATE_CONVERTING = "Converting"
    STATE_REMUXING = "Remuxing"
    STATE_COMPLETED = "Completed"
    STATE_FAILED = "Failed"
    STATE_CANCELLED = "Cancelled"

    state_changed = Signal(str, str)
    progress = Signal(float, str, str)          # percent, speed, eta
    title_resolved = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        *,
        yt_dlp: Path,
        ffmpeg: Path,
        output_dir: Path,
        video_quality: str = "best",
        audio_quality: str = "best",
        audio_only: bool = False,
        cookies_file: str = "",
        hardware: HardwareCapabilities | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.yt_dlp = Path(yt_dlp)
        self.ffmpeg = Path(ffmpeg)
        self.output_dir = Path(output_dir)
        self.video_quality = video_quality
        self.audio_quality = audio_quality
        self.audio_only = audio_only
        self.cookies_file = cookies_file
        self.hardware = hardware or CPU_FALLBACK

        self._temp_dir: Path | None = None
        self._downloaded_file: Path | None = None
        self._ytdlp_proc: QProcess | None = None
        self._ffmpeg_proc: QProcess | None = None
        self._ytdlp_buffer = ""
        self._ffmpeg_buffer = ""
        self._cancelled = False
        self._state = self.STATE_QUEUED
        self._title: str = ""
        self._job_log_path: Path | None = None

        # Time-windowed sample buffer: (monotonic_time, downloaded_bytes)
        self._samples: deque[tuple[float, float]] = deque()

        # UI throttling
        self._pending_progress: tuple[float, str, str] | None = None
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(_UI_THROTTLE_MS)
        self._ui_timer.timeout.connect(self._flush_progress)
        self._ui_timer.start()

        # Stall detection
        self._stall_timer = QTimer(self)
        self._stall_timer.setInterval(_STALL_MS)
        self._stall_timer.setSingleShot(True)
        self._stall_timer.timeout.connect(self._on_stall)

    # ---------------------------------------------------------------- public --

    def start(self) -> None:
        self._write_job_log_header()
        log.info("Starting download: %s", self.url)
        self._set_state(self.STATE_DOWNLOADING, "Starting download…")

        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="vdl-", dir=paths.temp_dir()))
        except OSError as exc:
            self._fail(f"Could not create temporary folder: {exc}")
            return

        proc = QProcess(self)
        proc.setProgram(str(self.yt_dlp))
        proc.setArguments(self._ytdlp_args())
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_ytdlp_output)
        proc.finished.connect(self._on_ytdlp_finished)
        proc.errorOccurred.connect(self._on_ytdlp_error)
        self._ytdlp_proc = proc
        log.info("yt-dlp argv: %s", proc.arguments())
        proc.start()
        self._stall_timer.start()

    def cancel(self) -> None:
        self._cancelled = True
        self._stall_timer.stop()
        self._ui_timer.stop()
        for proc in (self._ytdlp_proc, self._ffmpeg_proc):
            if proc is not None and proc.state() != QProcess.NotRunning:
                proc.kill()
        self._cleanup_temp()
        self._set_state(self.STATE_CANCELLED, "Cancelled")

    # --------------------------------------------------------------- yt-dlp --

    def _ytdlp_args(self) -> list[str]:
        out_template = str(self._temp_dir / "%(title)s [%(id)s].%(ext)s")
        progress_template = (
            "download:"
            f"{_PROGRESS_PREFIX}"
            "%(progress.downloaded_bytes)s|"
            "%(progress.total_bytes)s|"
            "%(progress.total_bytes_estimate)s"
        )
        args = [
            "--newline",
            "--no-color",
            "--no-warnings",
            "--progress",
            "--progress-template", progress_template,
            "-o", out_template,
            "--ffmpeg-location", str(self.ffmpeg),
            "--no-playlist",
            "--verbose",
            # --- network resilience -------------------------------------------
            "--socket-timeout", "15",
            "--retries", "20",
            "--fragment-retries", "20",
            "--file-access-retries", "5",
            "--retry-sleep", "linear=1::2",
            "--http-chunk-size", "1M",
            "--throttled-rate", "100K",
            "--buffer-size", "16K",
            "--continue",
            "--no-abort-on-error",
        ]
        if self.cookies_file and Path(self.cookies_file).exists():
            args += ["--cookies", self.cookies_file]

        if self.audio_only:
            args += ["-f", "bestaudio/best", "-x", "--audio-format", "m4a"]
        else:
            args += [
                "-f", VIDEO_FORMATS.get(self.video_quality, VIDEO_FORMATS["best"]),
                "--merge-output-format", "mp4",
            ]

        args.append(self.url)
        return args

    def _on_ytdlp_output(self) -> None:
        if self._ytdlp_proc is None:
            return
        chunk = bytes(self._ytdlp_proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._ytdlp_buffer += chunk
        self._stall_timer.start()

        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            log.debug("[yt-dlp] %s", stripped)
            self._handle_progress_line(stripped)

        for match in _DEST_RE.finditer(chunk):
            raw = match.group(1).strip().strip("'\"")
            stem = _FORMAT_SUFFIX_RE.sub("", Path(raw).stem)
            self._title = stem
            self.title_resolved.emit(self._title)
            # New file starting → reset progress sampling
            self._samples.clear()

        if len(self._ytdlp_buffer) > _MAX_BUFFER:
            self._ytdlp_buffer = self._ytdlp_buffer[-_MAX_BUFFER:]

    def _handle_progress_line(self, line: str) -> None:
        if not line.startswith(_PROGRESS_PREFIX):
            return
        payload = line[len(_PROGRESS_PREFIX):]
        parts = payload.split("|")
        if len(parts) < 3:
            return

        downloaded = _safe_float(parts[0])
        total_raw = _safe_float(parts[1])
        estimate = _safe_float(parts[2])
        total = total_raw if total_raw > 0 else estimate

        if downloaded <= 0:
            return

        now = time.monotonic()
        self._samples.append((now, downloaded))

        cutoff = now - _SPEED_WINDOW_SECONDS
        while len(self._samples) >= 2 and self._samples[1][0] < cutoff:
            self._samples.popleft()

        if len(self._samples) >= 2:
            t0, b0 = self._samples[0]
            t1, b1 = self._samples[-1]
            dt = t1 - t0
            db = b1 - b0
            speed = (db / dt) if dt > 0 and db >= 0 else 0.0
        else:
            speed = 0.0

        pct = (downloaded / total * 100.0) if total > 0 else 0.0
        eta = ((total - downloaded) / speed) if speed > 0 and total > downloaded else 0.0

        self._pending_progress = (pct, _fmt_speed(speed), _fmt_eta(eta))

    def _flush_progress(self) -> None:
        if self._pending_progress is not None and not self._cancelled:
            self.progress.emit(*self._pending_progress)
            self._pending_progress = None

    def _on_ytdlp_error(self, err: QProcess.ProcessError) -> None:
        if self._cancelled:
            return
        self._stall_timer.stop()
        self._fail(f"Could not launch yt-dlp: {err.name}")

    def _on_ytdlp_finished(self, exit_code: int, _status) -> None:
        self._stall_timer.stop()
        if self._cancelled:
            return
        if exit_code != 0:
            self._write_job_log("yt-dlp failed")
            self._fail(self._humanize_ytdlp_error(self._ytdlp_buffer))
            return

        file = self._find_downloaded_file()
        if file is None:
            self._write_job_log("no output file after yt-dlp exit")
            self._fail("Download finished but no output file was found.")
            return

        self._downloaded_file = file
        if not self._title:
            self._title = file.stem

        if self.audio_only:
            self._finalize()
            return

        action, reason = self._decide_conversion(file)
        log.info("Conversion decision: %s (%s)", action, reason)

        if action == "skip":
            self._finalize()
        elif action == "remux":
            self._start_conversion(remux_only=True)
        else:
            self._start_conversion(remux_only=False)

    def _decide_conversion(self, file: Path) -> tuple[str, str]:
        """
        Decide what to do with the downloaded file.

        Returns (action, reason) where action is one of:
          - "skip":    already H.264/AAC in MP4; move as-is
          - "remux":   H.264/AAC in a non-MP4 container; copy streams to MP4
          - "transcode": needs a full re-encode (VP9, AV1, HEVC, etc.)
        """
        info = probe_media(self.ffmpeg, file)
        if info is None:
            return "transcode", "probe failed (safe default)"

        v = info.video_codec
        a = info.audio_codec
        container = file.suffix.lower().lstrip(".")
        reason = f"{v}/{a or 'none'} in {container}"

        if v == "h264" and a in ("aac", "mp3", ""):
            if container == "mp4":
                return "skip", reason + " — already mp4/h264"
            if container in ("mkv", "mov", "flv"):
                return "remux", reason + " — remux to mp4"

        return "transcode", reason

    def _on_stall(self) -> None:
        if self._cancelled or self._state in (self.STATE_COMPLETED, self.STATE_FAILED):
            return
        log.warning("Download stalled for %ds, aborting: %s", _STALL_MS // 1000, self.url)
        self._write_job_log(f"stalled: no progress for {_STALL_MS // 1000}s")
        for proc in (self._ytdlp_proc, self._ffmpeg_proc):
            if proc is not None and proc.state() != QProcess.NotRunning:
                proc.kill()
        self._fail(
            f"The download stalled — no data received for {_STALL_MS // 1000} seconds. "
            "The server stopped responding. Retry the download."
        )

    @staticmethod
    def _humanize_ytdlp_error(text: str) -> str:
        t = text.lower()
        error_line = _extract_error_line(text)

        if "bytes read" in t and "more expected" in t:
            return (
                "The server closed the connection before the download finished. "
                "Retry the download; if it keeps happening, try a different quality."
            )
        if "read timed out" in t or "connection reset" in t:
            return "The connection was interrupted repeatedly. Check your network and retry."
        if "private video" in t:
            return "This video is private."
        if "video unavailable" in t or "this video is not available" in t:
            return "This video is unavailable."
        if "sign in to confirm" in t or "login required" in t or "cookies" in t:
            return "YouTube requires sign-in. Import a cookies.txt file and retry."
        if "unsupported url" in t:
            return "This URL is not supported."
        if "http error 404" in t:
            return "The video was not found (404)."
        if "http error 403" in t:
            return "Access forbidden (403). The video may be region-locked."
        if "timed out" in t or "temporary failure" in t:
            return "Network error. Check your connection and retry."
        if "requested format is not available" in t:
            return "The requested quality is not available for this video."
        if "ffmpeg not found" in t or "ffmpeg is not installed" in t:
            return "ffmpeg is missing or not executable. Reinstall components."
        if "postprocessing" in t or "merger" in t or "unable to merge" in t:
            return f"Post-processing failed: {error_line}" if error_line else "Post-processing failed."
        if "unable to download video data" in t:
            return "Could not download video data. The link may have expired."

        if error_line:
            return error_line

        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(_PROGRESS_PREFIX):
                continue
            if stripped.startswith("[download]"):
                continue
            return stripped
        return "Download failed."

    def _find_downloaded_file(self) -> Path | None:
        if self._temp_dir is None:
            return None
        candidates = [
            p for p in self._temp_dir.iterdir()
            if p.is_file()
            and not p.name.endswith((".part", ".ytdl", ".tmp"))
            and not p.name.startswith(".")
        ]
        if not candidates:
            return None
        if self.audio_only:
            for ext in (".m4a", ".mp3", ".opus", ".webm"):
                hit = next((p for p in candidates if p.suffix == ext), None)
                if hit:
                    return hit
        else:
            for ext in (".mp4", ".mkv", ".webm", ".mov"):
                hit = next((p for p in candidates if p.suffix == ext), None)
                if hit:
                    return hit
        return candidates[0]

    # --------------------------------------------------------------- ffmpeg --

    def _start_conversion(self, *, remux_only: bool = False) -> None:
        assert self._downloaded_file is not None
        assert self._temp_dir is not None

        if remux_only:
            self._set_state(self.STATE_REMUXING, "Repackaging to MP4 (no re-encode)…")
        else:
            self._set_state(
                self.STATE_CONVERTING,
                f"Converting to H.264/AAC ({self.hardware.label})…",
            )
        self.progress.emit(-1.0, "", "")
        self._stall_timer.start()

        out = self._temp_dir / "converted.mp4"

        if remux_only:
            args = [
                "-y",
                "-i", str(self._downloaded_file),
                "-c", "copy",
                "-sn",                     # drop subtitle streams (mp4 compat)
                "-dn",                     # drop data streams
                "-movflags", "+faststart",
                "-f", "mp4",
                str(out),
            ]
        else:
            args = [
                "-y",
                *decoder_args(self.hardware),
                "-i", str(self._downloaded_file),
                *encoder_args(self.hardware),
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(out),
            ]

        proc = QProcess(self)
        proc.setProgram(str(self.ffmpeg))
        proc.setArguments(args)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_ffmpeg_output)
        proc.finished.connect(self._on_ffmpeg_finished)
        proc.errorOccurred.connect(self._on_ffmpeg_error)
        self._ffmpeg_proc = proc
        log.info("ffmpeg argv: %s", proc.arguments())
        proc.start()

    def _on_ffmpeg_output(self) -> None:
        if self._ffmpeg_proc is None:
            return
        chunk = bytes(self._ffmpeg_proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._ffmpeg_buffer += chunk
        self._stall_timer.start()
        for line in chunk.splitlines():
            if line.strip():
                log.debug("[ffmpeg] %s", line.strip())
        if len(self._ffmpeg_buffer) > _MAX_BUFFER:
            self._ffmpeg_buffer = self._ffmpeg_buffer[-_MAX_BUFFER:]

    def _on_ffmpeg_error(self, err: QProcess.ProcessError) -> None:
        if self._cancelled:
            return
        self._stall_timer.stop()
        self._fail(f"Could not launch ffmpeg: {err.name}")

    def _on_ffmpeg_finished(self, exit_code: int, _status) -> None:
        self._stall_timer.stop()
        if self._cancelled:
            return
        if exit_code != 0 or self._temp_dir is None:
            self._write_job_log("ffmpeg failed")
            last = _last_nonblank(self._ffmpeg_buffer) or "unknown ffmpeg error"
            self._fail(f"Conversion failed: {last}")
            return
        converted = self._temp_dir / "converted.mp4"
        if not converted.exists():
            self._write_job_log("ffmpeg produced no output")
            self._fail("Conversion finished but the output file is missing.")
            return
        self._downloaded_file = converted
        self._finalize()

    # ------------------------------------------------------------- finalize --

    def _finalize(self) -> None:
        assert self._downloaded_file is not None
        self._stall_timer.stop()
        self._ui_timer.stop()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = sanitize_filename(self._title or self._downloaded_file.stem)
        suffix = self._downloaded_file.suffix or ".mp4"
        target = unique_path(self.output_dir / f"{safe_stem}{suffix}")

        try:
            shutil.move(str(self._downloaded_file), str(target))
        except OSError as exc:
            self._write_job_log(f"move failed: {exc}")
            self._fail(f"Could not move file to output folder: {exc}")
            return

        log.info("Completed: %s", target)
        self._cleanup_temp()
        self._set_state(self.STATE_COMPLETED, str(target))
        self.finished.emit(str(target))

    # --------------------------------------------------------------- helpers --

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self.state_changed.emit(state, message)

    def _fail(self, message: str) -> None:
        log.error("Job failed: %s — %s", self.url, message)
        self._stall_timer.stop()
        self._ui_timer.stop()
        self._cleanup_temp()
        self._set_state(self.STATE_FAILED, message)
        self.failed.emit(message)

    def _cleanup_temp(self) -> None:
        if self._temp_dir is not None and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None

    # ------------------------------------------------------------- job log ---

    def _write_job_log_header(self) -> None:
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", self.url)[:80]
            self._job_log_path = paths.logs_dir() / f"job-{stamp}-{safe}.log"
            self._job_log_path.write_text(
                f"URL: {self.url}\n"
                f"yt-dlp: {self.yt_dlp}\n"
                f"ffmpeg: {self.ffmpeg}\n"
                f"output_dir: {self.output_dir}\n"
                f"video_quality: {self.video_quality}\n"
                f"audio_only: {self.audio_only}\n"
                f"cookies: {self.cookies_file or '(none)'}\n"
                f"hardware_encoder: {self.hardware.encoder or 'libx264'}\n"
                f"hardware_decoder: {self.hardware.decoder or '(cpu)'}\n"
                f"\n----- yt-dlp output -----\n",
                encoding="utf-8",
            )
            log.info("Job log: %s", self._job_log_path)
        except OSError:
            log.exception("Could not create job log file")

    def _write_job_log(self, reason: str) -> None:
        if self._job_log_path is None:
            return
        try:
            with open(self._job_log_path, "a", encoding="utf-8") as f:
                f.write(self._ytdlp_buffer)
                if self._ffmpeg_buffer:
                    f.write("\n----- ffmpeg output -----\n")
                    f.write(self._ffmpeg_buffer)
                f.write(f"\n\n----- result -----\n{reason}\n")
        except OSError:
            log.exception("Could not write job log")


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #

def _safe_float(text: str) -> float:
    if not text:
        return 0.0
    text = text.strip()
    if text in ("NA", "None", "null", "-", ""):
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _fmt_speed(bps: float) -> str:
    """Format bytes-per-second using SI units (KB / MB / GB)."""
    if bps <= 0:
        return ""
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    while bps >= 1000.0 and i < len(units) - 1:
        bps /= 1000.0
        i += 1
    return f"{bps:.1f} {units[i]}"


def _fmt_eta(seconds: float) -> str:
    if seconds <= 0:
        return ""
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _extract_error_line(text: str) -> str:
    matches = list(_ERROR_RE.finditer(text))
    if not matches:
        return ""
    return matches[-1].group(1).strip()


def _last_nonblank(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""