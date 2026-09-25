from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from video_downloader import paths
from video_downloader.ui.main_window import MainWindow


def _setup_logging() -> None:
    log_file = paths.logs_dir() / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Video Downloader")
    app.setOrganizationName("VideoDownloader")
    win = MainWindow()
    win.show()
    return app.exec()
