from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_downloader.config import load as load_config
from video_downloader.core.binary_manager import BinaryError, BinaryManager


class _SetupWorker(QObject):
    progress = Signal(str, float)          # message, fraction (0..1; -1 = indeterminate)
    finished = Signal(dict)
    failed = Signal(str)
    update_available = Signal(str, str)    # name, new_version

    def __init__(self, manager: BinaryManager, force_check: bool = True):
        super().__init__()
        self.manager = manager
        self.force_check = force_check

    def run(self) -> None:
        try:
            resolved = self.manager.check_for_updates()
            for name, res in resolved.items():
                current = self.manager.installed_version(name)
                if current and current != res.version:
                    self.update_available.emit(name, res.version)

            paths = self.manager.ensure_all(
                progress_cb=self._on_progress,
                force_check=self.force_check,
            )
        except BinaryError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error while preparing components: {exc}")
            return
        self.finished.emit(paths)

    def _on_progress(self, message: str, fraction: float | None) -> None:
        self.progress.emit(message, -1.0 if fraction is None else fraction)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Downloader")
        self.resize(880, 620)
        self.cfg = load_config()

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.status = QLabel("Preparing video engine…", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)

        self.retry = QPushButton("Retry", self)
        self.retry.setEnabled(False)
        self.retry.clicked.connect(self._start_setup)
        layout.addWidget(self.retry)

        layout.addStretch(1)
        self.setCentralWidget(central)

        self._thread: QThread | None = None
        self._worker: _SetupWorker | None = None
        self._start_setup()

    def _start_setup(self) -> None:
        self.retry.setEnabled(False)
        self.status.setText("Preparing video engine…")
        self.bar.setRange(0, 0)

        manager = BinaryManager()
        thread = QThread(self)
        worker = _SetupWorker(manager)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.update_available.connect(self._on_update_available)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_progress(self, message: str, fraction: float) -> None:
        self.status.setText(message)
        if fraction < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(fraction * 100))

    def _on_finished(self, paths: dict) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        self.status.setText("Video engine ready.")
        self._teardown_thread()

    def _on_failed(self, message: str) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status.setText(f"Setup failed: {message}")
        self.retry.setEnabled(True)
        self._teardown_thread()

    def _on_update_available(self, name: str, version: str) -> None:
        self.status.setText(f"Updating {name} to {version}…")

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
            self._worker = None