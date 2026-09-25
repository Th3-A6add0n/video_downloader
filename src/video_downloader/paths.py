from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir, user_downloads_dir

APP_NAME = "VideoDownloader"
APP_AUTHOR = "Th3 A6add0n"


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


def config_file() -> Path:
    return data_dir() / "config.json"


def default_download_dir() -> Path:
    p = Path(user_downloads_dir()) / "VideoDownloader"
    p.mkdir(parents=True, exist_ok=True)
    return p