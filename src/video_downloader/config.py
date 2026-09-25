from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from video_downloader import paths


@dataclass
class Config:
    download_dir: str = ""
    theme: str = "system"              # "light" | "dark" | "system"
    language: str = "en"
    default_video_quality: str = "best"
    default_audio_quality: str = "best"
    audio_only: bool = False
    cookies_file: str = ""
    last_binary_check: float = 0.0

    def resolved_download_dir(self) -> Path:
        if self.download_dir:
            p = Path(self.download_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
        return paths.default_download_dir()


def load() -> Config:
    path = paths.config_file()
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config()
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in raw.items() if k in known})


def save(cfg: Config) -> None:
    path = paths.config_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)