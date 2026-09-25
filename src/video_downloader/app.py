from __future__ import annotations

import ctypes
import logging
import sys
from importlib.resources import files

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from video_downloader import paths
from video_downloader.ui.main_window import MainWindow

APP_USER_MODEL_ID = "org.videodownloader.video_downloader"


def _setup_logging() -> None:
    log_file = paths.logs_dir() / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )


def _set_app_user_model_id(app_id: str) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


def _load_app_icon() -> QIcon:
    """
    Load the app icon from package resources.

    Windows uses .ico, macOS uses .icns, Linux uses .png. Prefer the
    platform-specific extension, but fall back to .png if the specific
    format isn't available.
    """
    suffix = {".ico": "win32", ".icns": "darwin"}.get  # noqa: F841
    preferred = {
        "win32": "icon.ico",
        "darwin": "icon.icns",
    }.get(sys.platform, "icon.png")

    try:
        resource_dir = files("video_downloader.resources")
    except (ModuleNotFoundError, TypeError):
        return QIcon()

    for candidate in (preferred, "icon.png"):
        path = resource_dir.joinpath(candidate)
        try:
            if path.is_file():
                return QIcon(str(path))
        except (FileNotFoundError, AttributeError):
            continue
    return QIcon()


def main() -> int:
    _setup_logging()
    log = logging.getLogger(__name__)

    try:
        removed = paths.sweep_orphaned_temp()
        if removed:
            log.info("Removed %d orphaned temp director%s.",
                     removed, "y" if removed == 1 else "ies")
    except OSError as exc:
        log.warning("Could not sweep orphaned temp directories: %s", exc)

    _set_app_user_model_id(APP_USER_MODEL_ID)

    app = QApplication(sys.argv)
    app.setApplicationName("Video Downloader")
    app.setOrganizationName("VideoDownloader")
    app.setApplicationDisplayName("Video Downloader")
    app.setDesktopFileName(APP_USER_MODEL_ID)

    icon = _load_app_icon()
    if icon.isNull():
        log.warning("Application icon could not be loaded; the title bar will use the Qt default.")
    app.setWindowIcon(icon)

    win = MainWindow()
    win.show()
    return app.exec()