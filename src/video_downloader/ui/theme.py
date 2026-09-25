from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def _dark() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(37, 37, 38))
    p.setColor(QPalette.WindowText, QColor(228, 228, 228))
    p.setColor(QPalette.Base, QColor(30, 30, 30))
    p.setColor(QPalette.AlternateBase, QColor(45, 45, 48))
    p.setColor(QPalette.ToolTipBase, QColor(45, 45, 48))
    p.setColor(QPalette.ToolTipText, QColor(228, 228, 228))
    p.setColor(QPalette.Text, QColor(228, 228, 228))
    p.setColor(QPalette.Button, QColor(45, 45, 48))
    p.setColor(QPalette.ButtonText, QColor(228, 228, 228))
    p.setColor(QPalette.BrightText, QColor(255, 100, 100))
    p.setColor(QPalette.Link, QColor(80, 160, 240))
    p.setColor(QPalette.Highlight, QColor(0, 122, 204))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    return p


def _light() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(245, 245, 247))
    p.setColor(QPalette.WindowText, QColor(28, 28, 30))
    p.setColor(QPalette.Base, QColor(255, 255, 255))
    p.setColor(QPalette.AlternateBase, QColor(238, 238, 240))
    p.setColor(QPalette.Text, QColor(28, 28, 30))
    p.setColor(QPalette.Button, QColor(238, 238, 240))
    p.setColor(QPalette.ButtonText, QColor(28, 28, 30))
    p.setColor(QPalette.Highlight, QColor(0, 122, 204))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    return p


def apply_theme(app: QApplication, mode: str) -> None:
    app.setStyle("Fusion")
    if mode == "dark":
        app.setPalette(_dark())
    elif mode == "light":
        app.setPalette(_light())
    else:
        # "system": let Qt pick up the OS default
        app.setPalette(app.style().standardPalette())
