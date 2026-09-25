from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from video_downloader.core.downloader import DownloadJob


class DownloadItemWidget(QFrame):
    def __init__(self, job: DownloadJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self._final_path: str | None = None

        self.setFrameShape(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        self.title_label = QLabel(job.url, self)
        f = self.title_label.font()
        f.setBold(True)
        self.title_label.setFont(f)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.title_label.setWordWrap(True)
        outer.addWidget(self.title_label)

        self.url_label = QLabel(job.url, self)
        self.url_label.setStyleSheet("color: gray;")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.url_label)

        bar_row = QHBoxLayout()
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        bar_row.addWidget(self.bar, 1)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setFixedWidth(90)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bar_row.addWidget(self.cancel_btn)

        self.open_btn = QPushButton("Open folder", self)
        self.open_btn.setFixedWidth(110)
        self.open_btn.clicked.connect(self._on_open_folder)
        self.open_btn.hide()
        bar_row.addWidget(self.open_btn)

        outer.addLayout(bar_row)

        self.status_label = QLabel("Queued", self)
        self.status_label.setStyleSheet("color: gray;")
        outer.addWidget(self.status_label)

        job.state_changed.connect(self._on_state_changed)
        job.progress.connect(self._on_progress)
        job.title_resolved.connect(self._on_title_resolved)

    # --------------------------------------------------------------- slots --

    def _on_title_resolved(self, title: str) -> None:
        self.title_label.setText(title)

    def _on_progress(self, pct: float, speed: str, eta: str) -> None:
        if pct < 0:
            self.bar.setRange(0, 0)
            self.status_label.setText("Converting…")
            return
        self.bar.setRange(0, 100)
        self.bar.setValue(int(pct))
        parts = [f"{pct:5.1f}%"]
        if speed:
            parts.append(speed)
        if eta:
            parts.append(f"ETA {eta}")
        self.status_label.setText("  •  ".join(parts))

    def _on_state_changed(self, state: str, message: str) -> None:
        if state == DownloadJob.STATE_DOWNLOADING:
            self.status_label.setText("Downloading…")
            self.bar.setRange(0, 0)
        elif state == DownloadJob.STATE_CONVERTING:
            self.status_label.setText("Converting to H.264/AAC…")
            self.bar.setRange(0, 0)
        elif state == DownloadJob.STATE_COMPLETED:
            self._final_path = message
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
            self.status_label.setText(f"Completed — {message}")
            self.cancel_btn.hide()
            self.open_btn.show()
        elif state == DownloadJob.STATE_FAILED:
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            self.status_label.setText(f"Failed: {message}")
            self.cancel_btn.setText("Retry")
            self.cancel_btn.setEnabled(False)
        elif state == DownloadJob.STATE_CANCELLED:
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            self.status_label.setText("Cancelled.")
            self.cancel_btn.setEnabled(False)

    def _on_cancel(self) -> None:
        self.job.cancel()
        self.cancel_btn.setEnabled(False)

    def _on_open_folder(self) -> None:
        if self._final_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._final_path).parent)))