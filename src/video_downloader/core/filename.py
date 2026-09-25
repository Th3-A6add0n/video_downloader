from __future__ import annotations

import re
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, replacement: str = "_", max_length: int = 200) -> str:
    """Return a filesystem-safe version of `name` that preserves Unicode."""
    name = _ILLEGAL.sub(replacement, name)
    name = name.strip().rstrip(". ")
    if not name:
        name = "video"

    if "." in name:
        stem, _, ext = name.rpartition(".")
        if stem.upper() in _RESERVED:
            stem = "_" + stem
        name = f"{stem}.{ext}" if ext else stem
    else:
        if name.upper() in _RESERVED:
            name = "_" + name

    if len(name) > max_length:
        suffix = Path(name).suffix
        stem = name[: -len(suffix)] if suffix else name
        name = stem[: max_length - len(suffix)] + suffix

    return name


def unique_path(path: Path) -> Path:
    """Return a path that does not yet exist, appending ' (n)' if needed."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1