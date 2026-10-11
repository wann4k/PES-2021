from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from . import theme
from .main_window import MainWindow


def _asset(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "assets" / name


def app_icon() -> QIcon:
    if _asset("icon.png").is_file():
        return QIcon(str(_asset("icon.png")))
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.ACCENT))
    p.setPen(QColor(theme.ACCENT_DARK))
    p.drawRoundedRect(4, 14, 56, 36, 8, 8)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(22)
    p.setFont(f)
    p.setPen(QColor("#00170d"))
    p.drawText(pm.rect(), 0x84, "AD")  # AlignCenter
    p.end()
    return QIcon(pm)


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PES 2021 Adboard Studio")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(app_icon())
    if sys.platform == "win32":  # ikon taskbar sendiri, bukan ikon python
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PES2021.AdboardStudio")
    win = MainWindow()
    if len(sys.argv) > 1:
        win.open_folder(sys.argv[1])
    win.show()
    return app.exec()
