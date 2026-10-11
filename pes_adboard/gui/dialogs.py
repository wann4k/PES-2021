"""Dialog: ganti adboard, generator teks LED, restore backup."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
                               QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import library, textures
from ..media import FIT_MODES, IMAGE_EXTS, VIDEO_EXTS
from ..usm import CODEC_NAMES, UsmFile
from . import theme
from .player import ImageCanvas
from .workers import run_async

SOURCE_FILTER = ("Media (*.png *.jpg *.jpeg *.bmp *.tga *.webp *.gif *.dds *.mp4 *.mkv *.avi "
                 "*.mov *.webm *.wmv *.m4v *.mpg *.mpeg *.usm);;Semua file (*.*)")


def pil_to_qimage(img) -> QImage:
    img = img.convert("RGBA")
    return QImage(img.tobytes(), img.width, img.height, img.width * 4,
                  QImage.Format_RGBA8888).copy()


class DropZone(QFrame):
    fileDropped = Signal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAcceptDrops(True)
        self.setMinimumHeight(64)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        self.setStyleSheet(f"QFrame#card {{ border: 2px dashed {theme.BORDER}; }}")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(f"QFrame#card {{ border: 2px dashed {theme.ACCENT}; }}")

    def dragLeaveEvent(self, e):
        self.setStyleSheet(f"QFrame#card {{ border: 2px dashed {theme.BORDER}; }}")

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        urls = e.mimeData().urls()
        if urls:
            self.fileDropped.emit(urls[0].toLocalFile())

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(self, "Pilih gambar / video sumber", "",
                                              SOURCE_FILTER)
        if path:
            self.fileDropped.emit(path)


# =========================================================================== replace


class ReplaceDialog(QDialog):
    """Ganti satu atau banyak adboard (USM / DDS) dengan gambar atau video baru."""

    replaced = Signal()

    def __init__(self, items: list[library.Item], root: Path | None, source: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.items = [i for i in items if i.kind != library.KIND_FTEX]
        self.root = root
        self.source: Path | None = None
        self.setWindowTitle("Ganti Adboard")
        self.resize(980, 640)

        first = self.items[0]
        title = QLabel(f"Ganti {len(self.items)} adboard" if len(self.items) > 1
                       else f"Ganti: {first.name}")
        title.setObjectName("title")
        target_info = QLabel(self._target_text(first))
        target_info.setObjectName("muted")
        target_info.setWordWrap(True)

        self.drop = DropZone("⬇  Tarik & lepas gambar / video / USM ke sini\natau klik untuk memilih file")
        self.drop.fileDropped.connect(self.set_source)

        self.before = ImageCanvas()
        self.before.placeholder = "Memuat adboard asli…"
        self.after = ImageCanvas()
        self.after.placeholder = "Belum ada sumber"
        compare = QGridLayout()
        lb, la = QLabel("SEBELUM"), QLabel("SESUDAH")
        for lbl in (lb, la):
            lbl.setObjectName("badge")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedHeight(22)
        # adboard umumnya sangat lebar -> sebelum/sesudah ditumpuk vertikal
        compare.addWidget(lb, 0, 0)
        compare.addWidget(self.before, 1, 0)
        compare.addWidget(la, 2, 0)
        compare.addWidget(self.after, 3, 0)
        compare.setRowStretch(1, 1)
        compare.setRowStretch(3, 1)

        # ---------------- opsi
        self.fit = QComboBox()
        for k, v in FIT_MODES.items():
            self.fit.addItem(v, k)
        self.fit.setCurrentIndex(2)
        self.fit.currentIndexChanged.connect(self._update_after)

        self.quality = QSlider(Qt.Horizontal)
        self.quality.setRange(10, 100)
        self.quality.setValue(85)
        q_lbl = QLabel("85")
        self.quality.valueChanged.connect(lambda v: q_lbl.setText(str(v)))
        q_row = QHBoxLayout()
        q_row.addWidget(self.quality)
        q_row.addWidget(q_lbl)

        self.duration = QComboBox()
        self.duration.addItem("Sama dengan adboard asli", "original")
        self.duration.addItem("Sama dengan video sumber", "source")
        self.duration.addItem("Custom", "custom")
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(0.5, 600)
        self.seconds.setValue(10)
        self.seconds.setSuffix(" detik")
        self.seconds.setEnabled(False)
        self.duration.currentIndexChanged.connect(
            lambda _: self.seconds.setEnabled(self.duration.currentData() == "custom"))
        dur_row = QHBoxLayout()
        dur_row.addWidget(self.duration, 1)
        dur_row.addWidget(self.seconds)

        self.codec = QComboBox()
        self.codec.addItem("Auto (sama dengan USM asli) — disarankan", "auto")
        for k in ("mpeg1", "mpeg2", "h264", "vp9"):
            self.codec.addItem(CODEC_NAMES[k], k)
        self.loop = QCheckBox("Loop video sumber bila lebih pendek")
        self.loop.setChecked(True)

        self.dds_fmt = QComboBox()
        self.dds_fmt.addItem("Auto (sama dengan asli)", "auto")
        for f in textures.DDS_SAVE_FORMATS:
            self.dds_fmt.addItem(f, f)
        self.mips = QComboBox()
        self.mips.addItem("Auto (ikuti asli)", "auto")
        self.mips.addItem("Ya", "yes")
        self.mips.addItem("Tidak", "no")
        self.backup = QCheckBox("Backup file asli otomatis (folder _adboard_backup)")
        self.backup.setChecked(True)

        has_usm = any(i.kind == library.KIND_USM for i in self.items)
        has_tex = any(i.kind == library.KIND_TEXTURE for i in self.items)
        common = QGroupBox("Umum")
        f0 = QFormLayout(common)
        f0.addRow("Mode ukuran", self.fit)
        f0.addRow(self.backup)
        vid = QGroupBox("Video USM")
        f1 = QFormLayout(vid)
        f1.addRow("Kualitas", q_row)
        f1.addRow("Durasi", dur_row)
        f1.addRow("Codec", self.codec)
        f1.addRow(self.loop)
        vid.setVisible(has_usm)
        tex = QGroupBox("Tekstur DDS")
        f2 = QFormLayout(tex)
        f2.addRow("Format", self.dds_fmt)
        f2.addRow("Mipmap", self.mips)
        tex.setVisible(has_tex)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        self.log.setVisible(False)

        self.go = QPushButton("⚡  Terapkan")
        self.go.setObjectName("primary")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.apply)
        close = QPushButton("Tutup")
        close.clicked.connect(self.reject)

        left = QVBoxLayout()
        left.addWidget(title)
        left.addWidget(target_info)
        left.addWidget(self.drop)
        left.addLayout(compare, 1)
        right = QVBoxLayout()
        right.addWidget(common)
        right.addWidget(vid)
        right.addWidget(tex)
        right.addStretch()
        right.addWidget(self.progress)
        right.addWidget(self.log)
        btns = QHBoxLayout()
        btns.addStretch()
        btns.addWidget(close)
        btns.addWidget(self.go)
        right.addLayout(btns)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(430)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(rw)

        self._target_size = (0, 0)
        self._src_img = None
        run_async(lambda: (library.thumbnail(first, (1600, 900)), library.describe(first)),
                  self._got_before, lambda e: setattr(self.before, "placeholder", e))
        if source:
            self.set_source(source)

    def _target_text(self, item: library.Item) -> str:
        names = ", ".join(i.name for i in self.items[:4])
        more = f" (+{len(self.items) - 4} lagi)" if len(self.items) > 4 else ""
        return f"Target: {names}{more}\nResolusi, FPS & codec mengikuti tiap file target."

    def _got_before(self, result) -> None:
        img, info = result
        res = info.get("Resolusi", "0 x 0").split(" x ")
        self._target_size = (int(res[0]), int(res[1]))
        if img is not None:
            self.before.set_image(pil_to_qimage(img), reset=True)
        self._update_after()

    def set_source(self, path: str) -> None:
        p = Path(path)
        ext = p.suffix.lower()
        if ext not in IMAGE_EXTS | VIDEO_EXTS:
            QMessageBox.warning(self, "Format tidak didukung", f"File {p.name} tidak didukung.")
            return
        self.source = p
        self.drop.label.setText(f"🎬  {p.name}\n(klik / tarik file lain untuk mengganti)")
        self.after.placeholder = "Memuat sumber…"
        self.after.set_image(None)
        self.go.setEnabled(True)

        def load():
            src = library._Source(p)
            try:
                return src.first_frame()
            finally:
                src.close()

        run_async(load, self._got_source,
                  lambda e: (setattr(self.after, "placeholder", f"Gagal: {e}"), self.after.update()))

    def _got_source(self, img) -> None:
        self._src_img = img
        self._update_after()

    def _update_after(self, *_):
        if self._src_img is None or not all(self._target_size):
            return
        w, h = self._target_size
        fitted = textures.fit_image(self._src_img, w, h, self.fit.currentData())
        self.after.set_image(pil_to_qimage(fitted), reset=True)

    def options(self) -> library.ReplaceOptions:
        return library.ReplaceOptions(
            fit=self.fit.currentData(), quality=self.quality.value(),
            duration=self.duration.currentData(), seconds=self.seconds.value(),
            codec=self.codec.currentData(), dds_format=self.dds_fmt.currentData(),
            mipmaps=self.mips.currentData(), backup=self.backup.isChecked(),
            loop=self.loop.isChecked())

    def apply(self) -> None:
        if not self.source:
            return
        opts = self.options()
        self.go.setEnabled(False)
        self.progress.setVisible(True)
        self.log.setVisible(True)
        self.progress.setRange(0, len(self.items))
        self.progress.setValue(0)
        self._queue = list(self.items)
        self._errors = 0
        self._next(opts)

    def _next(self, opts: library.ReplaceOptions) -> None:
        if not self._queue:
            self.progress.setValue(self.progress.maximum())
            self.log.appendPlainText("✅ Selesai." if not self._errors
                                     else f"⚠ Selesai dengan {self._errors} error.")
            self.go.setEnabled(True)
            self.replaced.emit()
            return
        item = self._queue.pop(0)
        self.log.appendPlainText(f"⏳ {item.name} …")
        src = self.source

        def ok(msg):
            self.log.appendPlainText(f"   ✔ {msg}")
            self.progress.setValue(self.progress.value() + 1)
            self._next(opts)

        def fail(err):
            self._errors += 1
            self.log.appendPlainText(f"   ✖ {err}")
            self.progress.setValue(self.progress.value() + 1)
            self._next(opts)

        run_async(lambda: library.replace_item(item, src, opts, self.root), ok, fail)


# =========================================================================== LED


class ColorButton(QPushButton):
    changed = Signal(QColor)

    def __init__(self, color: str):
        super().__init__()
        self.color = QColor(color)
        self.setFixedWidth(64)
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self):
        self.setStyleSheet(f"background: {self.color.name()}; border: 1px solid {theme.BORDER};"
                           "border-radius: 6px; min-height: 22px;")

    def _pick(self):
        c = QColorDialog.getColor(self.color, self, "Pilih warna")
        if c.isValid():
            self.color = c
            self._paint()
            self.changed.emit(c)


class LedTextDialog(QDialog):
    """Generator adboard LED: teks berjalan / kedip / statis → USM atau DDS."""

    created = Signal()

    STYLES = [("scroll_left", "Berjalan ke kiri ←"), ("scroll_right", "Berjalan ke kanan →"),
              ("static", "Statis (tengah)"), ("blink", "Kedip"), ("slides", "Ganti-ganti teks (pisah dengan |)")]

    def __init__(self, target: library.Item | None, root: Path | None, parent=None):
        super().__init__(parent)
        self.target = target if target and target.kind != library.KIND_FTEX else None
        self.root = root
        self.setWindowTitle("Generator Teks LED")
        self.resize(1000, 640)
        self.t = 0.0

        w, h, fps, secs, codec = 1024, 128, 30.0, 10.0, "h264"
        self.template = None
        if self.target and self.target.kind == library.KIND_USM:
            try:
                self.template = UsmFile.load(self.target.path)
                i = self.template.info
                w, h, fps, secs = i.width, i.height, i.fps or 30.0, i.duration or 10.0
                if i.codec in CODEC_NAMES and i.codec != "unknown":
                    codec = i.codec
            except Exception:
                self.template = None
        elif self.target:
            ti = textures.texture_info(self.target.path)
            w, h = ti.width, ti.height

        self.text = QLineEdit("PES 2021  •  ADBOARD STUDIO  •  VISIT YOUR SPONSOR")
        self.font = QFontComboBox()
        self.font.setCurrentFont(QFont("Arial Black"))
        self.bold = QCheckBox("Tebal")
        self.bold.setChecked(True)
        self.italic = QCheckBox("Miring")
        self.size = QSpinBox()
        self.size.setRange(10, 120)
        self.size.setValue(62)
        self.size.setSuffix(" % tinggi")
        self.fg = ColorButton("#ffffff")
        self.bg1 = ColorButton("#0a3d91")
        self.bg2 = ColorButton("#021a45")
        self.outline = ColorButton("#000000")
        self.outline_w = QSpinBox()
        self.outline_w.setRange(0, 20)
        self.outline_w.setValue(0)
        self.style = QComboBox()
        for k, v in self.STYLES:
            self.style.addItem(v, k)
        self.speed = QSpinBox()
        self.speed.setRange(10, 2000)
        self.speed.setValue(180)
        self.speed.setSuffix(" px/detik")
        self.seamless = QCheckBox("Loop mulus (sesuaikan kecepatan otomatis)")
        self.seamless.setChecked(True)
        self.led = QCheckBox("Efek titik LED (dot-matrix)")
        self.led.setChecked(True)
        self.dot = QSpinBox()
        self.dot.setRange(2, 16)
        self.dot.setValue(4)
        self.dot.setSuffix(" px")
        self.glow = QCheckBox("Glow")
        self.glow.setChecked(True)
        self.width_s = QSpinBox()
        self.width_s.setRange(16, 8192)
        self.width_s.setValue(w)
        self.height_s = QSpinBox()
        self.height_s.setRange(16, 4096)
        self.height_s.setValue(h)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 120)
        self.fps.setValue(fps)
        self.secs = QDoubleSpinBox()
        self.secs.setRange(0.5, 600)
        self.secs.setValue(round(secs, 2))
        self.secs.setSuffix(" detik")
        self.codec = QComboBox()
        for k in ("h264", "vp9", "mpeg1", "mpeg2"):
            self.codec.addItem(CODEC_NAMES[k], k)
        self.codec.setCurrentIndex(max(0, self.codec.findData(codec)))
        locked = self.target is not None
        for wdg in (self.width_s, self.height_s):
            wdg.setEnabled(not locked)
        if self.template is not None:
            self.fps.setEnabled(False)
            self.codec.setEnabled(False)

        form = QFormLayout()
        form.addRow("Teks", self.text)
        frow = QHBoxLayout()
        frow.addWidget(self.font, 1)
        frow.addWidget(self.bold)
        frow.addWidget(self.italic)
        form.addRow("Font", frow)
        form.addRow("Ukuran", self.size)
        crow = QHBoxLayout()
        for lbl, b in (("Teks", self.fg), ("BG atas", self.bg1), ("BG bawah", self.bg2),
                       ("Outline", self.outline)):
            crow.addWidget(QLabel(lbl))
            crow.addWidget(b)
        crow.addWidget(self.outline_w)
        form.addRow("Warna", crow)
        form.addRow("Animasi", self.style)
        form.addRow("Kecepatan", self.speed)
        form.addRow(self.seamless)
        erow = QHBoxLayout()
        erow.addWidget(self.led)
        erow.addWidget(self.dot)
        erow.addWidget(self.glow)
        erow.addStretch()
        form.addRow("Efek", erow)
        rrow = QHBoxLayout()
        rrow.addWidget(self.width_s)
        rrow.addWidget(QLabel("x"))
        rrow.addWidget(self.height_s)
        form.addRow("Resolusi", rrow)
        form.addRow("FPS", self.fps)
        form.addRow("Durasi", self.secs)
        form.addRow("Codec (USM baru)", self.codec)

        self.canvas = ImageCanvas()
        self.canvas.setMinimumHeight(160)
        self.status = QLabel("")
        self.status.setObjectName("muted")

        out_box = QHBoxLayout()
        self.btn_replace = QPushButton(f"⚡ Ganti {self.target.name}" if self.target else "Ganti target")
        self.btn_replace.setObjectName("primary")
        self.btn_replace.setEnabled(self.target is not None)
        self.btn_replace.clicked.connect(self.replace_target)
        b_usm = QPushButton("💾 Simpan USM baru…")
        b_usm.clicked.connect(self.save_usm)
        b_img = QPushButton("🖼 Simpan gambar (DDS/PNG)…")
        b_img.clicked.connect(self.save_image)
        close = QPushButton("Tutup")
        close.clicked.connect(self.reject)
        out_box.addWidget(self.status, 1)
        out_box.addWidget(close)
        out_box.addWidget(b_img)
        out_box.addWidget(b_usm)
        out_box.addWidget(self.btn_replace)

        lay = QVBoxLayout(self)
        title = QLabel("💡 Generator Teks LED")
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(form)
        lay.addLayout(out_box)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        self._dot_cache: tuple | None = None

    # -------------------------------------------------------------- render
    def _font(self, h: int) -> QFont:
        f = QFont(self.font.currentFont())
        f.setPixelSize(max(4, int(h * self.size.value() / 100)))
        f.setBold(self.bold.isChecked())
        f.setItalic(self.italic.isChecked())
        return f

    def _segments(self) -> list[str]:
        return [s.strip() for s in self.text.text().split("|") if s.strip()] or [" "]

    def _speed(self, w: int, h: int) -> float:
        speed = float(self.speed.value())
        if not self.seamless.isChecked():
            return speed
        fm = QFontMetricsF(self._font(h))
        period = fm.horizontalAdvance(self._text_line()) + w * 0.25
        dur = self.secs.value()
        cycles = max(1, round(speed * dur / period))
        return period * cycles / dur

    def _text_line(self) -> str:
        return "     ".join(self._segments())

    def render_frame(self, t: float, w: int, h: int) -> QImage:
        img = QImage(w, h, QImage.Format_RGB888)
        p = QPainter(img)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, self.bg1.color)
        g.setColorAt(1, self.bg2.color)
        p.fillRect(0, 0, w, h, g)
        font = self._font(h)
        fm = QFontMetricsF(font)
        style = self.style.currentData()
        segs = self._segments()
        texts: list[tuple[str, float]] = []
        if style in ("scroll_left", "scroll_right"):
            line = self._text_line()
            period = fm.horizontalAdvance(line) + w * 0.25
            off = (t * self._speed(w, h)) % period
            x0 = -off if style == "scroll_left" else off - period
            x = x0
            while x < w:
                texts.append((line, x))
                x += period
        elif style == "blink":
            if (t * 2) % 2 < 1.4:
                texts.append((segs[0], (w - fm.horizontalAdvance(segs[0])) / 2))
        elif style == "slides":
            per = max(0.5, self.secs.value() / len(segs))
            s = segs[int(t / per) % len(segs)]
            texts.append((s, (w - fm.horizontalAdvance(s)) / 2))
        else:
            texts.append((segs[0], (w - fm.horizontalAdvance(segs[0])) / 2))

        y = (h + fm.ascent() - fm.descent()) / 2
        path = QPainterPath()
        for s, x in texts:
            path.addText(QPointF(x, y), font, s)
        if self.glow.isChecked():
            glow = QColor(self.fg.color)
            for width, alpha in ((h * 0.16, 30), (h * 0.09, 50), (h * 0.04, 80)):
                glow.setAlpha(alpha)
                p.strokePath(path, QPen(glow, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.outline_w.value():
            p.strokePath(path, QPen(self.outline.color, self.outline_w.value() * 2,
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, self.fg.color)
        if self.led.isChecked():
            p.drawPixmap(0, 0, self._dot_mask(w, h))
        p.end()
        return img

    def _dot_mask(self, w: int, h: int) -> QPixmap:
        d = self.dot.value()
        key = (w, h, d)
        if self._dot_cache and self._dot_cache[0] == key:
            return self._dot_cache[1]
        tile = QPixmap(d, d)
        tile.fill(QColor(0, 0, 0, 150))
        tp = QPainter(tile)
        tp.setRenderHint(QPainter.Antialiasing)
        tp.setCompositionMode(QPainter.CompositionMode_Clear)
        tp.drawEllipse(QRectF(0.5, 0.5, d - 1, d - 1))
        tp.end()
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.drawTiledPixmap(0, 0, w, h, tile)
        p.end()
        self._dot_cache = (key, pm)
        return pm

    def _tick(self) -> None:
        self.t += 0.033
        if self.t > self.secs.value():
            self.t = 0.0
        w, h = self.width_s.value(), self.height_s.value()
        self.canvas.set_image(self.render_frame(self.t, w, h))
        self.status.setText(f"{w}x{h} • {self.fps.value():g} fps • t={self.t:05.2f}s"
                            + (f" • kecepatan efektif {self._speed(w, h):.0f} px/s"
                               if self.style.currentData().startswith("scroll") else ""))

    def _frames(self) -> list[bytes]:
        w, h = self.width_s.value(), self.height_s.value()
        fps = self.fps.value()
        n = max(1, round(self.secs.value() * fps))
        out = []
        for i in range(n):
            out.append(_packed(self.render_frame(i / fps, w, h), 3))
        return out

    # -------------------------------------------------------------- output
    def _busy(self, on: bool, msg: str = "") -> None:
        self.setEnabled(not on)
        self.status.setText(msg)
        if on:
            self.timer.stop()
        else:
            self.timer.start(33)

    def _encode_to(self, out: Path, template: UsmFile | None, after=None) -> None:
        w, h = self.width_s.value(), self.height_s.value()
        fps = self.fps.value()
        codec = template.info.codec if template is not None else self.codec.currentData()
        self._busy(True, "⏳ Merender & encode…")
        frames = self._frames()

        def job():
            library.create_usm_from_frames(frames, w, h, fps, out, codec, 90, template)
            return out

        def ok(path):
            self._busy(False, f"✅ Tersimpan: {path}")
            self.created.emit()
            if after:
                after()

        def fail(e):
            self._busy(False, "")
            QMessageBox.critical(self, "Gagal", e)

        run_async(job, ok, fail)

    def replace_target(self) -> None:
        if not self.target:
            return
        if self.target.kind == library.KIND_USM:
            if self.template is None:
                QMessageBox.warning(self, "USM", "USM target tidak bisa dibaca.")
                return
            library.make_backup(self.target.path, self.root)
            self._encode_to(self.target.path, self.template)
        else:
            library.make_backup(self.target.path, self.root)
            img = self._still()
            ti = textures.texture_info(self.target.path)
            textures.save_texture(img, self.target.path, ti.save_format, ti.mipmaps > 1)
            self.status.setText(f"✅ {self.target.name} diganti (backup dibuat).")
            self.created.emit()

    def _still(self):
        from PIL import Image
        w, h = self.width_s.value(), self.height_s.value()
        img = self.render_frame(self.t, w, h).convertToFormat(QImage.Format_RGBA8888)
        return Image.frombytes("RGBA", (w, h), _packed(img, 4))

    def save_usm(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Simpan USM", "led_adboard.usm", "USM (*.usm)")
        if path:
            self._encode_to(Path(path), None)

    def save_image(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Simpan gambar", "led_adboard.dds",
                                              "DDS (*.dds);;PNG (*.png)")
        if path:
            textures.save_texture(self._still(), path, "DXT5", True)
            self.status.setText(f"✅ Tersimpan: {path}")
            self.created.emit()


def _packed(img: QImage, bpp: int) -> bytes:
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    raw = bytes(img.constBits())
    if bpl == w * bpp:
        return raw[: w * h * bpp]
    return b"".join(raw[y * bpl: y * bpl + w * bpp] for y in range(h))


# =========================================================================== backup


class BackupDialog(QDialog):
    restored = Signal()

    def __init__(self, item: library.Item, root: Path | None, parent=None):
        super().__init__(parent)
        self.item, self.root = item, root
        self.setWindowTitle(f"Backup — {item.name}")
        self.resize(560, 360)
        self.list = QListWidget()
        for b in library.list_backups(item.path, root):
            li = QListWidgetItem(f"🕘 {b.name}")
            li.setData(Qt.UserRole, str(b))
            self.list.addItem(li)
        if not self.list.count():
            self.list.addItem("Belum ada backup untuk file ini.")
        btns = QDialogButtonBox()
        restore = btns.addButton("Restore", QDialogButtonBox.AcceptRole)
        restore.setObjectName("primary")
        btns.addButton("Tutup", QDialogButtonBox.RejectRole)
        btns.accepted.connect(self._restore)
        btns.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pilih versi backup untuk dikembalikan:"))
        lay.addWidget(self.list)
        lay.addWidget(btns)

    def _restore(self) -> None:
        li = self.list.currentItem()
        path = li.data(Qt.UserRole) if li else None
        if not path:
            return
        library.make_backup(self.item.path, self.root)  # simpan versi sekarang juga
        library.restore_backup(self.item.path, Path(path))
        self.restored.emit()
        self.accept()
