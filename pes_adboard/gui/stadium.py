"""Simulasi tampilan adboard di pinggir lapangan (kamera broadcast)."""
from __future__ import annotations

import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF, QRadialGradient, QTransform)
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from . import theme


def _lerp(a: QPointF, b: QPointF, t: float) -> QPointF:
    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)


class StadiumCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 240)
        self.image: QImage | None = None
        self.repeat = 4
        self.camera = "broadcast"
        self.mode = "night"
        self._crowd: QPixmap | None = None
        self._crowd_key = None

    def set_image(self, img: QImage | None) -> None:
        self.image = img
        self.update()

    # ------------------------------------------------------------------ scene
    def _crowd_pixmap(self, w: int, h: int) -> QPixmap:
        key = (w, h, self.mode)
        if self._crowd is not None and self._crowd_key == key:
            return self._crowd
        rnd = random.Random(7)
        pm = QPixmap(max(1, w), max(1, h))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        palette = ["#d8dee9", "#e63946", "#1d3557", "#f1c40f", "#2ecc71", "#ffffff",
                   "#457b9d", "#8d99ae", "#ff7f50"]
        rows = max(6, h // 7)
        for r in range(rows):
            y = h * r / rows
            size = 2.0 + 3.5 * (r / rows)
            x = rnd.random() * size
            while x < w:
                c = QColor(rnd.choice(palette))
                c.setAlpha(140 if self.mode == "night" else 200)
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x, y + rnd.random() * 2), size * 0.5, size * 0.6)
                x += size * (1.1 + rnd.random())
        p.end()
        self._crowd, self._crowd_key = pm, key
        return pm

    def _draw_board(self, p: QPainter, quad: list[QPointF], segments: int) -> None:
        """Gambar tile adboard di sepanjang quad (tl, tr, br, bl)."""
        tl, tr, br, bl = quad
        img = self.image
        if img is None or img.isNull():
            p.setBrush(QColor("#111"))
            p.drawPolygon(QPolygonF(quad))
            return
        src = QPolygonF([QPointF(0, 0), QPointF(img.width(), 0),
                         QPointF(img.width(), img.height()), QPointF(0, img.height())])
        for i in range(segments):
            a, b = i / segments, (i + 1) / segments
            dst = QPolygonF([_lerp(tl, tr, a), _lerp(tl, tr, b), _lerp(bl, br, b), _lerp(bl, br, a)])
            t = QTransform()
            if QTransform.quadToQuad(src, dst, t):
                p.save()
                p.setTransform(t, True)
                p.drawImage(QPointF(0, 0), img)
                p.restore()
        # bingkai + kilau LED
        p.setPen(QPen(QColor(0, 0, 0, 200), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF(quad))
        if self.mode == "night":
            glow = QLinearGradient(tl, bl)
            glow.setColorAt(0, QColor(255, 255, 255, 40))
            glow.setColorAt(0.5, QColor(255, 255, 255, 0))
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF(quad))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        night = self.mode == "night"

        if self.camera == "closeup":
            self._paint_closeup(p, W, H, night)
            return

        horizon = H * 0.42
        # langit / atap stadion
        sky = QLinearGradient(0, 0, 0, horizon)
        sky.setColorAt(0, QColor("#05070c" if night else "#7fb3e6"))
        sky.setColorAt(1, QColor("#1a2233" if night else "#c9def0"))
        p.fillRect(QRectF(0, 0, W, horizon), sky)
        # tribun
        stand = QLinearGradient(0, H * 0.08, 0, horizon)
        stand.setColorAt(0, QColor("#141a24" if night else "#5a6372"))
        stand.setColorAt(1, QColor("#232c3a" if night else "#7d8796"))
        p.fillRect(QRectF(0, H * 0.08, W, horizon - H * 0.08), stand)
        p.drawPixmap(0, int(H * 0.09), self._crowd_pixmap(W, int(horizon - H * 0.11)))
        if night:  # lampu stadion
            for x in (0.12, 0.38, 0.62, 0.88):
                g = QRadialGradient(QPointF(W * x, H * 0.04), W * 0.12)
                g.setColorAt(0, QColor(255, 255, 240, 160))
                g.setColorAt(1, QColor(255, 255, 240, 0))
                p.setBrush(g)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(W * x, H * 0.04), W * 0.12, W * 0.12)

        # lapangan (trapesium perspektif)
        far_y, near_y = horizon + H * 0.075, H * 1.05
        far_l, far_r = W * 0.06, W * 0.94
        near_l, near_r = -W * 0.55, W * 1.55
        stripes = 14
        for i in range(stripes):
            a, b = i / stripes, (i + 1) / stripes
            # interpolasi perspektif sederhana (lebih rapat di jauh)
            ya = far_y + (near_y - far_y) * (a ** 1.6)
            yb = far_y + (near_y - far_y) * (b ** 1.6)
            la = far_l + (near_l - far_l) * (a ** 1.6)
            lb = far_l + (near_l - far_l) * (b ** 1.6)
            ra = far_r + (near_r - far_r) * (a ** 1.6)
            rb = far_r + (near_r - far_r) * (b ** 1.6)
            col = QColor("#2f7d3a" if i % 2 else "#368a42")
            if night:
                col = col.darker(115)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(la, ya), QPointF(ra, ya), QPointF(rb, yb),
                                     QPointF(lb, yb)]))
        # sisi luar lapangan
        p.setBrush(QColor("#2a6e33").darker(130 if night else 110))
        p.drawRect(QRectF(0, horizon, W, far_y - horizon + 1))

        # garis lapangan
        p.setPen(QPen(QColor(255, 255, 255, 210), 2))
        p.setBrush(Qt.NoBrush)
        line_y = far_y + 3
        p.drawLine(QPointF(far_l, line_y), QPointF(far_r, line_y))
        p.drawLine(QPointF(W / 2, line_y), QPointF(W / 2, H))
        p.drawEllipse(QPointF(W / 2, H * 0.80), W * 0.17, H * 0.12)
        p.drawLine(QPointF(far_l, line_y), QPointF(near_l, near_y))
        p.drawLine(QPointF(far_r, line_y), QPointF(near_r, near_y))

        # adboard tepi lapangan
        bh = H * 0.06
        top = horizon + H * 0.005
        board = [QPointF(-2, top), QPointF(W + 2, top), QPointF(W + 2, top + bh),
                 QPointF(-2, top + bh)]
        self._draw_board(p, board, self.repeat)
        # pantulan/glow di rumput
        if night and self.image is not None:
            g = QLinearGradient(0, top + bh, 0, top + bh + H * 0.06)
            g.setColorAt(0, QColor(255, 255, 255, 45))
            g.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillRect(QRectF(0, top + bh, W, H * 0.06), g)

        # vignette
        vg = QRadialGradient(QPointF(W / 2, H / 2), max(W, H) * 0.75)
        vg.setColorAt(0.6, QColor(0, 0, 0, 0))
        vg.setColorAt(1, QColor(0, 0, 0, 150))
        p.fillRect(self.rect(), vg)
        self._hud(p, W, H)

    def _paint_closeup(self, p: QPainter, W: int, H: int, night: bool) -> None:
        bg = QLinearGradient(0, 0, 0, H)
        bg.setColorAt(0, QColor("#0b0f16" if night else "#9cb8d6"))
        bg.setColorAt(0.55, QColor("#1b2230" if night else "#6f7f92"))
        bg.setColorAt(0.56, QColor("#2a6e33"))
        bg.setColorAt(1, QColor("#3c9a4a"))
        p.fillRect(self.rect(), bg)
        p.drawPixmap(0, 0, self._crowd_pixmap(W, int(H * 0.35)))
        # papan menyamping dengan perspektif kuat
        quad = [QPointF(W * 0.02, H * 0.30), QPointF(W * 1.4, H * 0.20),
                QPointF(W * 1.4, H * 0.62), QPointF(W * 0.02, H * 0.55)]
        self._draw_board(p, quad, max(1, self.repeat // 2))
        path = QPainterPath()
        path.addRect(QRectF(0, H * 0.62, W, H))
        p.fillPath(path, QBrush(QColor(0, 0, 0, 40)))
        self._hud(p, W, H)

    def _hud(self, p: QPainter, W: int, H: int) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(QRectF(14, 14, 168, 26), 6, 6)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(14, 14, 44, 26), 6, 6)
        p.setPen(QColor("#00170d"))
        f = p.font()
        f.setBold(True)
        f.setPointSizeF(9)
        p.setFont(f)
        p.drawText(QRectF(14, 14, 44, 26), Qt.AlignCenter, "LIVE")
        p.setPen(QColor("white"))
        p.drawText(QRectF(62, 14, 120, 26), Qt.AlignVCenter, "HOME  1 - 0  AWAY")


class StadiumPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvas = StadiumCanvas()
        cam = QComboBox()
        cam.addItem("Kamera broadcast", "broadcast")
        cam.addItem("Close-up pinggir lapangan", "closeup")
        cam.currentIndexChanged.connect(lambda _: self._set("camera", cam.currentData()))
        mode = QComboBox()
        mode.addItem("Malam (LED)", "night")
        mode.addItem("Siang", "day")
        mode.currentIndexChanged.connect(lambda _: self._set("mode", mode.currentData()))
        rep = QSpinBox()
        rep.setRange(1, 12)
        rep.setValue(self.canvas.repeat)
        rep.setPrefix("Ulang ")
        rep.setSuffix("x")
        rep.valueChanged.connect(lambda v: self._set("repeat", v))
        hint = QLabel("Simulasi kasar — hanya untuk melihat komposisi & keterbacaan.")
        hint.setObjectName("muted")
        bar = QHBoxLayout()
        bar.addWidget(cam)
        bar.addWidget(mode)
        bar.addWidget(rep)
        bar.addStretch()
        bar.addWidget(hint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(bar)

    def _set(self, attr: str, value) -> None:
        setattr(self.canvas, attr, value)
        self.canvas.update()

    def set_image(self, img: QImage | None) -> None:
        self.canvas.set_image(img)
