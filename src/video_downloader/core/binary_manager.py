from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from video_downloader import paths
from video_downloader.core.sources import (
    EvermeetFfmpegResolver,
    ResolvedBinary,
    Resolver,
    default_resolvers,
)

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, float | None], None]
CHUNK = 64 * 1024


class BinaryError(Exception):
    """Human-readable error for binary provisioning failures."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip(name: str, n: int) -> str | None:
    if not name:
        return None
    parts = Path(name).parts
    if len(parts) <= n:
        return None
    return str(Path(*parts[n:]))


def _extract(archive: Path, dest: Path, kind: str, strip: int) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if kind == "none":
        return
    if kind == "zip":
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                rel = _strip(member.filename, strip)
                if rel is None:
                    continue
                target = dest / rel
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        return
    modes = {"tar.xz": "r:xz", "tar.gz": "r:gz", "tar.bz2": "r:bz2", "tar": "r"}
    if kind not in modes:
        raise BinaryError(f"Unsupported archive type: {kind}")
    with tarfile.open(archive, modes[kind]) as t:
        members = []
        for m in t.getmembers():
            rel = _strip(m.name, strip)
            if rel is None:
                continue
            m.name = rel
            members.append(m)
        t.extractall(dest, members=members, filter="data")


def _flatten_executables(root: Path, executables: list[str]) -> None:
    for exe in executables:
        found = next((p for p in root.rglob(exe) if p.is_file()), None)
        if found is None:
            raise BinaryError(f"Archive did not contain expected file: {exe}")
        target = root / exe
        if found != target:
            if target.exists():
                target.unlink()
            shutil.move(str(found), str(target))


def _chmod_executables(root: Path, executables: list[str]) -> None:
    if os.name == "nt":
        return
    for exe in executables:
        p = root / exe
        if p.exists():
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #

class BinaryManager:
    def __init__(self, resolvers: dict[str, Resolver] | None = None):
        self.resolvers = resolvers or default_resolvers()
        self._lock_file = paths.binaries_dir() / ".lock"

    # -- public API ---------------------------------------------------------- #

    def check_for_updates(self) -> dict[str, ResolvedBinary]:
        """
        Resolve the latest version of every managed binary without installing.
        Returns a mapping name -> ResolvedBinary.
        """
        resolved: dict[str, ResolvedBinary] = {}
        for name, resolver in self.resolvers.items():
            try:
                resolved[name] = resolver.resolve()
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not resolve %s: %s", name, exc)
        return resolved

    def ensure_all(
        self,
        progress_cb: ProgressCb | None = None,
        force_check: bool = True,
    ) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for name in self.resolvers:
            out[name] = self.ensure(name, progress_cb=progress_cb, force_check=force_check)
        return out

    def ensure(
        self,
        name: str,
        progress_cb: ProgressCb | None = None,
        force_check: bool = True,
    ) -> Path:
        resolver = self.resolvers.get(name)
        if resolver is None:
            raise BinaryError(f"No resolver registered for {name}")

        if force_check or self.installed_version(name) is None:
            resolved = resolver.resolve()
        else:
            resolved = self._installed_as_resolved(name)
            if resolved is None:
                resolved = resolver.resolve()

        install_dir = paths.binaries_dir() / name / resolved.version
        current_file = paths.binaries_dir() / name / "current.json"

        if self.installed_version(name) == resolved.version and self._valid(install_dir, resolved):
            return install_dir / resolved.executables[0]

        with self._lock():
            if self.installed_version(name) == resolved.version and self._valid(install_dir, resolved):
                return install_dir / resolved.executables[0]
            self._install(name, resolved, install_dir, current_file, progress_cb)

        return install_dir / resolved.executables[0]

    def installed_version(self, name: str) -> str | None:
        cur = paths.binaries_dir() / name / "current.json"
        if not cur.exists():
            return None
        try:
            return json.loads(cur.read_text(encoding="utf-8")).get("version")
        except (OSError, json.JSONDecodeError):
            return None

    def installed_dir(self, name: str) -> Path | None:
        version = self.installed_version(name)
        if not version:
            return None
        p = paths.binaries_dir() / name / version
        return p if p.exists() else None

    def executable(self, name: str, exe: str | None = None) -> Path | None:
        d = self.installed_dir(name)
        if d is None:
            return None
        if exe is None:
            for candidate in ("yt-dlp", "yt-dlp.exe", "ffmpeg", "ffmpeg.exe"):
                p = d / candidate
                if p.exists():
                    return p
            return None
        p = d / exe
        return p if p.exists() else None

    # -- internals ----------------------------------------------------------- #

    def _installed_as_resolved(self, name: str) -> ResolvedBinary | None:
        version = self.installed_version(name)
        if version is None:
            return None
        d = paths.binaries_dir() / name / version
        if not d.exists():
            return None
        executables = [p.name for p in d.iterdir() if p.is_file()]
        return ResolvedBinary(
            name=name,
            version=version,
            url="",
            archive="none",
            executables=executables,
            source="installed",
        )

    def _valid(self, install_dir: Path, resolved: ResolvedBinary) -> bool:
        if not install_dir.exists():
            return False
        return all((install_dir / exe).exists() for exe in resolved.executables)

    def _lock(self):
        return _FileLock(self._lock_file)

    def _install(
        self,
        name: str,
        resolved: ResolvedBinary,
        install_dir: Path,
        current_file: Path,
        progress_cb: ProgressCb | None,
    ) -> None:
        bin_root = paths.binaries_dir() / name
        bin_root.mkdir(parents=True, exist_ok=True)
        tmp_root = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=bin_root))

        try:
            self._report(progress_cb, f"Downloading {name} {resolved.version}…", 0.0)
            dl = tmp_root / "download.bin"
            self._download(resolved.url, dl, progress_cb)

            if resolved.sha256:
                self._report(progress_cb, f"Verifying {name}…", None)
                actual = _sha256(dl)
                if actual.lower() != resolved.sha256.lower():
                    raise BinaryError(
                        f"Checksum mismatch for {name}. The download may be "
                        f"corrupt or tampered with."
                    )
            else:
                log.warning("No sha256 available for %s %s (%s); skipping verification.",
                            name, resolved.version, resolved.source)

            self._report(progress_cb, f"Extracting {name}…", None)
            extract_dir = tmp_root / "extract"
            if resolved.archive == "none":
                extract_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dl, extract_dir / resolved.executables[0])
            else:
                _extract(dl, extract_dir, resolved.archive, resolved.strip_components)
                _flatten_executables(extract_dir, resolved.executables)
            _chmod_executables(extract_dir, resolved.executables)

            # macOS: evermeet ships ffmpeg and ffprobe separately.
            if (
                name == "ffmpeg"
                and isinstance(self.resolvers.get("ffmpeg"), EvermeetFfmpegResolver)
                and "ffprobe" in resolved.executables
                and not (extract_dir / "ffprobe").exists()
            ):
                self._fetch_evermeet_ffprobe(extract_dir, resolved.version, progress_cb)

            self._report(progress_cb, f"Installing {name}…", None)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            os.replace(str(extract_dir), str(install_dir))

            self._write_current(current_file, resolved.version)
            self._report(progress_cb, f"{name} ready.", 1.0)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def _fetch_evermeet_ffprobe(
        self, extract_dir: Path, version: str, progress_cb: ProgressCb | None
    ) -> None:
        resolver = self.resolvers["ffmpeg"]
        if not isinstance(resolver, EvermeetFfmpegResolver):
            return
        try:
            ffprobe = resolver.resolve_ffprobe()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not resolve ffprobe from evermeet: %s", exc)
            return
        self._report(progress_cb, "Downloading ffprobe…", None)
        tmp = extract_dir / ".ffprobe.zip"
        self._download(ffprobe.url, tmp, progress_cb)
        _extract(tmp, extract_dir, "zip", 0)
        tmp.unlink(missing_ok=True)
        _chmod_executables(extract_dir, ["ffprobe"])

    def _download(self, url: str, dest: Path, progress_cb: ProgressCb | None) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "VideoDownloader/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            read = 0
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                read += len(chunk)
                if progress_cb and total:
                    progress_cb("downloading", read / total)

    def _write_current(self, path: Path, version: str) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": version}, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _report(cb: ProgressCb | None, message: str, fraction: float | None) -> None:
        if cb:
            cb(message, fraction)


class _FileLock:
    """Best-effort cross-platform advisory file lock."""

    def __init__(self, path: Path):
        self.path = path
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "w")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fd.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        return self

    def __exit__(self, *exc):
        try:
            if self._fd is not None:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            if self._fd is not None:
                self._fd.close()
                self._fd = None