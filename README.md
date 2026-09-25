# Video Downloader

A cross-platform desktop app for downloading videos from YouTube and other
platforms. Uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) as the download
backend and [ffmpeg](https://ffmpeg.org/) for conversion to NLE-friendly
H.264/AAC in an MP4 container.

Runs on **Windows**, **macOS**, and **Linux**. Distributed as a self-provisioning
app — `yt-dlp` and `ffmpeg` are fetched from upstream on first launch and kept
up to date automatically.

Licensed under **GPLv3**.

---

## Features

- Download single URLs, multiple URLs, or whole playlists.
- Best-quality default, or pick resolution / audio quality manually.
- Audio-only mode.
- Automatic conversion to H.264 + AAC MP4 (`+faststart`) for editing software.
- Per-download progress with cancel.
- Light and dark themes.
- Full keyboard navigation.
- Unicode-safe filenames.
- Self-updating `yt-dlp` and `ffmpeg` binaries.

## Requirements

- **Runtime:** none. The packaged app bundles its Python runtime and Qt.
- **Build:** Python 3.12+, `briefcase`.
- **Network:** required on first run to fetch `yt-dlp` and `ffmpeg`.

## Install

Download the installer for your platform from the [Releases page](../../releases):

| Platform | Format |
| -------- | ------ |
| Windows  | `.msi` (or a wrapped `.exe` installer) |
| macOS    | `.dmg` containing `.app` |
| Linux    | `.AppImage` |

On first launch the app downloads the platform-specific `yt-dlp` and `ffmpeg`
binaries into your user data directory and verifies them. Nothing is installed
system-wide.

## Cookies and YouTube sign-in

YouTube sometimes requires a signed-in session for age-restricted or
region-locked videos. The app supports two mechanisms:

1. **Browser cookies** — the app can read cookies directly from your installed
   browser. Select the browser in **Settings → Cookies**.
2. **`cookies.txt`** — export cookies from your browser using a
   [Netscape-format cookie exporter](https://github.com/rotemdan/ExportCookies)
   and import the file via **Settings → Import cookies.txt**.

The `cookies.txt` file is read locally and never uploaded anywhere.

## Where files go

| What | Location |
| ---- | -------- |
| Downloads | `~/Downloads/VideoDownloader` (configurable) |
| Config    | `<user data dir>/VideoDownloader/config.json` |
| Binaries  | `<user data dir>/VideoDownloader/bin/` |
| Logs      | `<user data dir>/VideoDownloader/logs/app.log` |

`<user data dir>` is the standard per-OS location:

- Windows: `%LOCALAPPDATA%\VideoDownloader\VideoDownloader`
- macOS: `~/Library/Application Support/VideoDownloader`
- Linux: `~/.local/share/VideoDownloader`

## Building from source

```bash
git clone https://github.com/yourname/video-downloader.git
cd video-downloader

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run without packaging:
briefcase dev

# Build a native installer for your current platform:
briefcase create
briefcase build
briefcase package
```

Artifacts appear under `dist/`.

## Release process

1. Bump `version` in `pyproject.toml` (and `__version__` in
   `src/video_downloader/__init__.py`).
2. Commit and push.
3. Tag the commit: `git tag v1.0.0 && git push --tags`.
4. GitHub Actions builds installers for all three platforms and attaches them
   to a draft release.
5. Review the draft, then publish.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Briefcase app bundle (Python + PySide6 + app code)  │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│  BinaryManager                                       │
│    - resolve()  ← Resolver   (queries upstream APIs) │
│    - ensure()   → download, verify, install, mark    │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│  User data dir / VideoDownloader / bin /             │
│    yt-dlp / <version> / current.json                 │
│    ffmpeg / <version> / current.json                 │
└──────────────────────────────────────────────────────┘
```

- `core/sources.py` contains one resolver per binary per platform. Resolvers
  query upstream release APIs at runtime; no versions are hard-coded.
- `core/binary_manager.py` handles download, SHA-256 verification, extraction,
  atomic install, and version bookkeeping.
- On every app launch, `check_for_updates()` resolves the current upstream
  version. If it differs from what's installed, the manager updates.

## Third-party licenses

The app bundles no third-party binaries. At runtime it downloads:

- **yt-dlp** (Unlicense) — https://github.com/yt-dlp/yt-dlp
- **FFmpeg** (GPL or LGPL depending on the build) — https://ffmpeg.org/legal.html

The Windows and Linux FFmpeg builds used are BtbN's GPL builds. If you
redistribute this app and rely on those builds, ensure your distribution
complies with the GPL. The macOS build comes from evermeet.cx.

## License

GPLv3 — see [LICENSE](LICENSE).