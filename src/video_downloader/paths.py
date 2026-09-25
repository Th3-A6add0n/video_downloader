from __future__ import annotations

import shutil
import time
from pathlib import Path

from platformdirs import user_data_dir, user_downloads_dir

APP_NAME = "VideoDownloader"
APP_AUTHOR = "Th3-A6add0n"

TEMP_PREFIX = "vdl-"


def data_dir() -> Path:
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def binaries_dir() -> Path:
    p = data_dir() / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_dir() -> Path:
    p = data_dir() / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_file() -> Path:
    return data_dir() / "config.json"


def default_download_dir() -> Path:
    p = Path(user_downloads_dir()) / "VideoDownloader"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sweep_orphaned_temp(max_age_seconds: int = 24 * 3600) -> int:
    """
    Remove temp directories left behind by previous crashes.

    Only directories starting with TEMP_PREFIX and older than
    `max_age_seconds` are deleted. Returns the number of directories removed.

    Safe to call on every startup — legitimate in-flight jobs never exceed
    a few hours, so anything older than 24h is guaranteed to be orphaned.
    """
    root = temp_dir()
    cutoff = time.time() - max_age_seconds
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith(TEMP_PREFIX):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            # If the directory vanished between iterdir() and stat(), ignore.
            pass
    return removed
