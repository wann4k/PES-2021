"""Canvas zoom/pan + player video untuk preview adboard."""
from __future__ import annotations

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSlider, QStyle, QToolButton,
                               QVBoxLayout, QWidget)

from ..library import Preview
from . import theme


def checkerboard(size: int = 12) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(QColor("#2a2f38"))
    p = QPainter(pm)
    p.fillRect(0, 0, size, size, QColor("#353b46"))
    p.fillRect(size, size, size, size, QColor("#353b46"))
    p.end()
    return pm


class ImageCanvas(QWidget):
    """Menampilkan QImage dengan zoom (scroll), pan (drag) dan mode channel."""

    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.image: QImage | None = None
        self.zoom = 1.0
        self.fit = True
        self.offset = QPointF(0, 0)
        self.channel = "rgba"
        self._drag: QPointF | None = None
        self._checker = checkerboard()
        self.placeholder = "Pilih file adboard di galeri"
        self.setMouseTracking(True)

    def set_image(self, img: QImage | None, reset: bool = False) -> None:
        self.image = img
        if reset:
            self.fit = True
            self.offset = QPointF(0, 0)
        self.update()

    def set_channel(self, ch: str) -> None:
        self.channel = ch
        self.update()

    def _scale(self) -> float:
        if not self.image or self.image.isNull():
            return 1.0
        if self.fit:
            return min((self.width() - 24) / self.image.width(),
                       (self.height() - 24) / self.image.height())
        return self.zoom

    def set_zoom(self, z: float) -> None:
        self.fit = False
        self.zoom = max(0.05, min(32.0, z))
        self.zoomChanged.emit(self.zoom)
        self.update()

    def fit_view(self) -> None:
        self.fit = True
        self.offset = QPointF(0, 0)
        self.zoomChanged.emit(self._scale())
        self.update()

    def wheelEvent(self, e):
        z = self._scale() * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        self.set_zoom(z)

    def mousePressEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._drag = e.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.offset += e.position() - self._drag
            self._drag = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, e):
        self.fit_view() if not self.fit else self.set_zoom(1.0)

    def _channel_image(self) -> QImage:
        img = self.image
        if self.channel == "rgba" or img is None:
            return img
        src = img.convertToFormat(QImage.Format_ARGB32)
        if self.channel == "rgb":
            return src.convertToFormat(QImage.Format_RGB32)
        if self.channel == "alpha":
            return alpha_view(img)
        return img

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        if not self.image or self.image.isNull():
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.placeholder)
            return
        s = self._scale()
        w, h = self.image.width() * s, self.image.height() * s
        x = (self.width() - w) / 2 + (0 if self.fit else self.offset.x())
        y = (self.height() - h) / 2 + (0 if self.fit else self.offset.y())
        target = QRectF(x, y, w, h)
        p.drawTiledPixmap(target, self._checker)
        p.setRenderHint(QPainter.SmoothPixmapTransform, s < 2)
        p.drawImage(target, self._channel_image())
        p.setPen(QColor(theme.BORDER))
        p.drawRect(target)


def alpha_view(img: QImage) -> QImage:
    """Channel alpha sebagai gambar grayscale."""
    src = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = src.width(), src.height()
    arr = np.frombuffer(src.constBits(), np.uint8).reshape(h, src.bytesPerLine())
    alpha = np.ascontiguousarray(arr[:, :w * 4].reshape(h, w, 4)[:, :, 3])
    return QImage(alpha.data, w, h, w, QImage.Format_Grayscale8).copy()


class PlayerPanel(QWidget):
    """Canvas + kontrol play/pause/seek untuk preview USM maupun tekstur."""

    frameChanged = Signal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = ImageCanvas()
        self.preview: Preview | None = None
        self._cache: dict[int, QImage] = {}
        self.index = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

        self.play_btn = QToolButton()
        self._icon_play = self.style().standardIcon(QStyle.SP_MediaPlay)
        self._icon_pause = self.style().standardIcon(QStyle.SP_MediaPause)
        self.play_btn.setIcon(self._icon_play)
        self.play_btn.setFixedWidth(40)
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self.seek)
        self.time_lbl = QLabel("—")
        self.time_lbl.setObjectName("muted")
        self.time_lbl.setMinimumWidth(130)
        self.speed = QComboBox()
        self.speed.addItems(["0.25x", "0.5x", "1x", "1.5x", "2x"])
        self.speed.setCurrentText("1x")
        self.speed.currentTextChanged.connect(self._restart_timer)
        self.loop_btn = QToolButton(text="🔁")
        self.loop_btn.setCheckable(True)
        self.loop_btn.setChecked(True)
        self.loop_btn.setToolTip("Loop")
        self.channel = QComboBox()
        self.channel.addItems(["RGBA", "RGB", "Alpha"])
        self.channel.currentTextChanged.connect(lambda t: self.canvas.set_channel(t.lower()))
        fit_btn = QToolButton(text="Fit")
        fit_btn.setToolTip("Fit ke layar (double-click canvas = 100%)")
        fit_btn.clicked.connect(self.canvas.fit_view)
        self.zoom_lbl = QLabel("Fit")
        self.zoom_lbl.setObjectName("muted")
        self.canvas.zoomChanged.connect(lambda z: self.zoom_lbl.setText(f"{z * 100:.0f}%"))

        controls = QHBoxLayout()
        for w in (self.play_btn, self.slider, self.time_lbl, self.speed, self.loop_btn):
            controls.addWidget(w)
        controls.addSpacing(10)
        controls.addWidget(self.channel)
        controls.addWidget(fit_btn)
        controls.addWidget(self.zoom_lbl)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(controls)
        self._set_video_controls(False)

    # ------------------------------------------------------------------ api
    def clear(self, message: str = "Pilih file adboard di galeri") -> None:
        self.timer.stop()
        self.preview = None
        self._cache.clear()
        self.canvas.placeholder = message
        self.canvas.set_image(None, reset=True)
        self.time_lbl.setText("—")
        self._set_video_controls(False)

    def set_preview(self, pv: Preview) -> None:
        self.timer.stop()
        self.preview = pv
        self._cache.clear()
        self.index = 0
        self._set_video_controls(pv.is_video)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(pv.frames) - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self._show(0, reset=True)
        if pv.is_video:
            self.play()

    def current_image(self) -> QImage | None:
        return self._image(self.index) if self.preview else None

    def play(self) -> None:
        if self.preview and self.preview.is_video:
            self._restart_timer()
            self.play_btn.setIcon(self._icon_pause)

    def pause(self) -> None:
        self.timer.stop()
        self.play_btn.setIcon(self._icon_play)

    def toggle(self) -> None:
        self.pause() if self.timer.isActive() else self.play()

    def seek(self, i: int) -> None:
        if self.preview:
            self._show(i)

    # -------------------------------------------------------------- internal
    def _set_video_controls(self, on: bool) -> None:
        for w in (self.play_btn, self.slider, self.speed, self.loop_btn):
            w.setEnabled(on)

    def _restart_timer(self, *_):
        if not self.preview or not self.preview.is_video:
            return
        speed = float(self.speed.currentText().rstrip("x"))
        self.timer.start(max(5, int(1000 / (self.preview.fps * speed))))

    def _image(self, i: int) -> QImage:
        img = self._cache.get(i)
        if img is None:
            pv = self.preview
            data = pv.frames[i]
            if pv.is_video:
                img = QImage(data, pv.width, pv.height, pv.width * 3, QImage.Format_RGB888).copy()
            else:
                img = QImage(data, pv.width, pv.height, pv.width * 4,
                             QImage.Format_RGBA8888).copy()
            if len(self._cache) > 120:
                self._cache.pop(next(iter(self._cache)))
            self._cache[i] = img
        return img

    def _show(self, i: int, reset: bool = False) -> None:
        pv = self.preview
        self.index = i
        img = self._image(i)
        self.canvas.set_image(img, reset)
        if pv.is_video:
            total = len(pv.frames)
            t = i / pv.fps if pv.fps else 0
            self.time_lbl.setText(f"{i + 1}/{total}  •  {t:05.2f}s")
            if self.slider.value() != i:
                self.slider.blockSignals(True)
                self.slider.setValue(i)
                self.slider.blockSignals(False)
        else:
            self.time_lbl.setText(f"{pv.width} x {pv.height}")
        self.frameChanged.emit(img)

    def _tick(self) -> None:
        pv = self.preview
        if not pv:
            return
        nxt = self.index + 1
        if nxt >= len(pv.frames):
            if not self.loop_btn.isChecked():
                self.pause()
                return
            nxt = 0
        self._show(nxt)
