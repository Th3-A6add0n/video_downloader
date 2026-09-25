from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from video_downloader.config import Config
from video_downloader.config import load as load_config
from video_downloader.config import save as save_config
from video_downloader.core.binary_manager import BinaryError, BinaryManager
from video_downloader.core.downloader import DownloadJob
from video_downloader.core.hardware import CPU_FALLBACK
from video_downloader.core.hardware import detect as detect_hardware
from video_downloader.ui.download_item import DownloadItemWidget
from video_downloader.ui.theme import apply_theme

VIDEO_QUALITIES = ["best", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]
AUDIO_QUALITIES = ["best", "320k", "256k", "192k", "128k"]


# --------------------------------------------------------------------------- #
# Setup worker (binary provisioning)
# --------------------------------------------------------------------------- #

class _SetupWorker(QObject):
    progress = Signal(str, float)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, manager: BinaryManager):
        super().__init__()
        self.manager = manager

    def run(self) -> None:
        try:
            paths = self.manager.ensure_all(progress_cb=self._on_progress)
        except BinaryError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error while preparing components: {exc}")
            return
        self.finished.emit(paths)

    def _on_progress(self, message: str, fraction: float | None) -> None:
        self.progress.emit(message, -1.0 if fraction is None else fraction)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Downloader")
        self.resize(960, 720)
        self.cfg: Config = load_config()

        self._yt_dlp: Path | None = None
        self._ffmpeg: Path | None = None
        self._hardware = CPU_FALLBACK
        self._setup_thread: QThread | None = None
        self._setup_worker: _SetupWorker | None = None
        self._jobs: list[DownloadJob] = []

        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._build_setup_view())
        self.stack.addWidget(self._build_main_view())

        self._build_toolbar()
        self._apply_config_to_ui()

        apply_theme(QApplication.instance(), self.cfg.theme)
        self._start_setup()

    # ------------------------------------------------------------- toolbar --

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        self.theme_action = QAction("Toggle theme", self)
        self.theme_action.triggered.connect(self._toggle_theme)
        tb.addAction(self.theme_action)

        tb.addSeparator()

        self.cookies_action = QAction("Import cookies.txt…", self)
        self.cookies_action.triggered.connect(self._import_cookies)
        tb.addAction(self.cookies_action)

        self.clear_cookies_action = QAction("Clear cookies", self)
        self.clear_cookies_action.triggered.connect(self._clear_cookies)
        tb.addAction(self.clear_cookies_action)

    # ------------------------------------------------------------ setup UI --

    def _build_setup_view(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addStretch(1)

        self.setup_status = QLabel("Preparing video engine…", w)
        self.setup_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.setup_status)

        self.setup_bar = QProgressBar(w)
        self.setup_bar.setRange(0, 0)
        layout.addWidget(self.setup_bar)

        self.setup_retry = QPushButton("Retry", w)
        self.setup_retry.setEnabled(False)
        self.setup_retry.clicked.connect(self._start_setup)
        layout.addWidget(self.setup_retry, alignment=Qt.AlignCenter)

        layout.addStretch(1)
        return w

    def _start_setup(self) -> None:
        self.setup_retry.setEnabled(False)
        self.setup_status.setText("Preparing video engine…")
        self.setup_bar.setRange(0, 0)

        manager = BinaryManager()
        thread = QThread(self)
        worker = _SetupWorker(manager)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_setup_progress)
        worker.finished.connect(self._on_setup_finished)
        worker.failed.connect(self._on_setup_failed)

        self._setup_thread = thread
        self._setup_worker = worker
        thread.start()

    def _on_setup_progress(self, message: str, fraction: float) -> None:
        self.setup_status.setText(message)
        if fraction < 0:
            self.setup_bar.setRange(0, 0)
        else:
            self.setup_bar.setRange(0, 100)
            self.setup_bar.setValue(int(fraction * 100))

    def _on_setup_finished(self, paths: dict) -> None:
        self._yt_dlp = paths.get("yt-dlp")
        self._ffmpeg = paths.get("ffmpeg")
        if self._yt_dlp is None or self._ffmpeg is None:
            self._on_setup_failed("Binary setup did not produce yt-dlp and ffmpeg paths.")
            return

        # Detect hardware encoders once, now that ffmpeg is available.
        self.setup_status.setText("Detecting hardware acceleration…")
        self.setup_bar.setRange(0, 0)
        QApplication.processEvents()
        try:
            self._hardware = detect_hardware(self._ffmpeg)
        except Exception as exc:  # noqa: BLE001
            self._hardware = CPU_FALLBACK
            print(f"Hardware detection failed: {exc}")

        self.hardware_label.setText(f"Encoding: {self._hardware.label}")

        self.setup_bar.setRange(0, 100)
        self.setup_bar.setValue(100)
        self.stack.setCurrentIndex(1)
        self._teardown_setup_thread()

    def _on_setup_failed(self, message: str) -> None:
        self.setup_bar.setRange(0, 100)
        self.setup_bar.setValue(0)
        self.setup_status.setText(f"Setup failed: {message}")
        self.setup_retry.setEnabled(True)
        self._teardown_setup_thread()

    def _teardown_setup_thread(self) -> None:
        if self._setup_thread is not None:
            self._setup_thread.quit()
            self._setup_thread.wait(2000)
            self._setup_thread = None
            self._setup_worker = None

    # ------------------------------------------------------------- main UI --

    def _build_main_view(self) -> QWidget:
        w = QWidget(self)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Video or playlist URL(s) — one per line:", w))
        self.url_input = QPlainTextEdit(w)
        self.url_input.setPlaceholderText(
            "https://www.youtube.com/watch?v=...\n"
            "https://www.youtube.com/playlist?list=..."
        )
        self.url_input.setFixedHeight(90)
        layout.addWidget(self.url_input)

        options = QFrame(w)
        options.setFrameShape(QFrame.StyledPanel)
        form = QFormLayout(options)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        self.video_quality = QComboBox(options)
        for q in VIDEO_QUALITIES:
            self.video_quality.addItem("Best available" if q == "best" else q, q)
        form.addRow("Video quality:", self.video_quality)

        self.audio_quality = QComboBox(options)
        for q in AUDIO_QUALITIES:
            self.audio_quality.addItem("Best available" if q == "best" else q, q)
        form.addRow("Audio quality:", self.audio_quality)

        self.audio_only = QCheckBox("Download audio only (no video)", options)
        form.addRow("", self.audio_only)

        out_row = QWidget(options)
        out_layout = QHBoxLayout(out_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        self.output_dir = QLineEdit(out_row)
        self.output_dir.setReadOnly(True)
        self.browse_btn = QPushButton("Browse…", out_row)
        self.browse_btn.clicked.connect(self._browse_output_dir)
        out_layout.addWidget(self.output_dir, 1)
        out_layout.addWidget(self.browse_btn)
        form.addRow("Output folder:", out_row)

        self.hardware_label = QLabel("Encoding: detecting…", options)
        self.hardware_label.setStyleSheet("color: gray;")
        form.addRow("", self.hardware_label)

        layout.addWidget(options)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.add_btn = QPushButton("Add Downloads", w)
        self.add_btn.setDefault(True)
        self.add_btn.setMinimumWidth(160)
        self.add_btn.clicked.connect(self._on_add_downloads)
        btn_row.addWidget(self.add_btn)
        layout.addLayout(btn_row)

        layout.addWidget(QLabel("Downloads:", w), 0)
        self.scroll = QScrollArea(w)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.list_container = QWidget(self.scroll)
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll, 1)

        return w

    def _apply_config_to_ui(self) -> None:
        vq = self.cfg.default_video_quality
        idx = self.video_quality.findData(vq)
        if idx >= 0:
            self.video_quality.setCurrentIndex(idx)

        aq = self.cfg.default_audio_quality
        idx = self.audio_quality.findData(aq)
        if idx >= 0:
            self.audio_quality.setCurrentIndex(idx)

        self.audio_only.setChecked(self.cfg.audio_only)
        self.output_dir.setText(str(self.cfg.resolved_download_dir()))

    # ---------------------------------------------------------- interaction --

    def _toggle_theme(self) -> None:
        order = {"system": "light", "light": "dark", "dark": "system"}
        self.cfg.theme = order.get(self.cfg.theme, "system")
        apply_theme(QApplication.instance(), self.cfg.theme)
        save_config(self.cfg)

    def _import_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select cookies.txt", "", "Cookies files (*.txt);;All files (*)"
        )
        if path:
            self.cfg.cookies_file = path
            save_config(self.cfg)
            QMessageBox.information(self, "Cookies imported", f"Using cookies from:\n{path}")

    def _clear_cookies(self) -> None:
        self.cfg.cookies_file = ""
        save_config(self.cfg)
        QMessageBox.information(self, "Cookies cleared", "Browser cookies will not be used.")

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select output folder", str(self.cfg.resolved_download_dir())
        )
        if path:
            self.cfg.download_dir = path
            save_config(self.cfg)
            self.output_dir.setText(path)

    def _on_add_downloads(self) -> None:
        if self._yt_dlp is None or self._ffmpeg is None:
            QMessageBox.warning(self, "Not ready", "The video engine is still preparing.")
            return

        urls = self._parse_urls(self.url_input.toPlainText())
        if not urls:
            QMessageBox.information(self, "No URLs", "Enter at least one URL.")
            return

        self.cfg.default_video_quality = self.video_quality.currentData()
        self.cfg.default_audio_quality = self.audio_quality.currentData()
        self.cfg.audio_only = self.audio_only.isChecked()
        save_config(self.cfg)

        out_dir = self.cfg.resolved_download_dir()

        for url in urls:
            job = DownloadJob(
                url,
                yt_dlp=self._yt_dlp,
                ffmpeg=self._ffmpeg,
                output_dir=out_dir,
                video_quality=self.video_quality.currentData(),
                audio_quality=self.audio_quality.currentData(),
                audio_only=self.audio_only.isChecked(),
                cookies_file=self.cfg.cookies_file,
                hardware=self._hardware,
                parent=self,
            )
            widget = DownloadItemWidget(job, self.list_container)
            self.list_layout.insertWidget(self.list_layout.count() - 1, widget)
            self._jobs.append(job)
            job.start()

        self.url_input.clear()

    @staticmethod
    def _parse_urls(text: str) -> list[str]:
        parts = re.split(r"[\s,]+", text)
        seen = set()
        out = []
        for p in parts:
            p = p.strip()
            if not p or p in seen:
                continue
            if not p.lower().startswith(("http://", "https://")):
                continue
            seen.add(p)
            out.append(p)
        return out
