"""Jendela utama PES 2021 Adboard Studio."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSlider, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolBar, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__, library
from ..media import FfmpegError, ffmpeg_exe
from . import theme
from .dialogs import BackupDialog, LedTextDialog, ReplaceDialog, pil_to_qimage
from .player import PlayerPanel
from .stadium import StadiumPanel
from .workers import run_async

KIND_LABEL = {library.KIND_USM: "USM", library.KIND_TEXTURE: "IMG", library.KIND_FTEX: "FTEX"}
KIND_COLOR = {library.KIND_USM: theme.ACCENT, library.KIND_TEXTURE: "#5aa9ff",
              library.KIND_FTEX: "#ffb454"}


def badge_pixmap(img: QImage | None, kind: str, ext: str, size: QSize) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(QColor(theme.PANEL_2))
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    if img is not None and not img.isNull():
        scaled = img.scaled(size.width() - 8, size.height() - 8, Qt.KeepAspectRatio,
                            Qt.SmoothTransformation)
        p.drawImage((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2,
                    scaled)
    else:
        p.setPen(QColor(theme.MUTED))
        p.drawText(pm.rect(), Qt.AlignCenter, "⏳" if img is None else "∅")
    label = ext.upper().lstrip(".") if kind == library.KIND_TEXTURE else KIND_LABEL[kind]
    f = QFont()
    f.setBold(True)
    f.setPointSize(7)
    p.setFont(f)
    w = p.fontMetrics().horizontalAdvance(label) + 10
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(KIND_COLOR[kind]))
    p.drawRoundedRect(4, 4, w, 15, 4, 4)
    p.setPen(QColor("#00170d"))
    p.drawText(4, 4, w, 15, Qt.AlignCenter, label)
    p.end()
    return pm


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"PES 2021 Adboard Studio {__version__}")
        self.resize(1440, 860)
        self.setAcceptDrops(True)
        self.settings = QSettings("PESAdboardStudio", "AdboardStudio")
        self.root: Path | None = None
        self.items: list[library.Item] = []
        self.current: library.Item | None = None
        self._thumbs: dict[str, QImage] = {}
        self._gen = 0
        self._preview_gen = 0
        self.thumb_size = QSize(232, 96)

        self._build_toolbar()
        self._build_ui()
        self.statusBar().showMessage("Siap. Buka folder adboard untuk mulai.")
        try:
            ffmpeg_exe()
        except FfmpegError as e:
            self.statusBar().showMessage(f"⚠ {e}")
        last = self.settings.value("last_folder", "")
        if last and Path(last).is_dir():
            self.open_folder(last)

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> None:
        tb = QToolBar("Utama")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        logo = QLabel("  ⚽ <b style='color:%s'>ADBOARD</b> STUDIO  " % theme.ACCENT)
        logo.setStyleSheet("font-size: 13pt;")
        tb.addWidget(logo)
        tb.addSeparator()

        def act(text, slot, shortcut=None, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        act("📂 Buka Folder", self.choose_folder, "Ctrl+O", "Buka folder berisi .usm / .dds")
        self.recent_btn = QPushButton("🕘 Terakhir ▾")
        self.recent_menu = QMenu(self)
        self.recent_btn.setMenu(self.recent_menu)
        self.recent_btn.setFlat(True)
        tb.addWidget(self.recent_btn)
        act("🔄 Refresh", self.refresh, "F5")
        tb.addSeparator()
        self.a_replace = act("⚡ Ganti Adboard", self.replace_selected, "Ctrl+R",
                             "Ganti adboard terpilih dengan gambar/video (bisa banyak sekaligus)")
        self.a_led = act("💡 Teks LED", self.led_generator, "Ctrl+L",
                         "Buat adboard teks berjalan ala LED")
        self.a_export = act("📤 Export", self.export_selected, "Ctrl+E",
                            "Export ke MP4 / PNG / frame / DDS")
        self.a_backup = act("🕘 Backup/Restore", self.backup_selected, "Ctrl+B")
        tb.addSeparator()
        act("ℹ Tentang", self.about)
        self._update_recent()

    def _build_ui(self) -> None:
        # ---- kiri: struktur folder
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(lambda *_: self.apply_filter())
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 0, 8)
        lt = QLabel("FOLDER")
        lt.setObjectName("muted")
        ll.addWidget(lt)
        ll.addWidget(self.tree)
        left.setMinimumWidth(190)

        # ---- tengah: galeri
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎  Cari nama file…")
        self.search.textChanged.connect(self.apply_filter)
        self.type_filter = QComboBox()
        self.type_filter.addItem("Semua tipe", None)
        self.type_filter.addItem("Video USM", library.KIND_USM)
        self.type_filter.addItem("Tekstur / gambar", library.KIND_TEXTURE)
        self.type_filter.addItem("FTEX", library.KIND_FTEX)
        self.type_filter.currentIndexChanged.connect(self.apply_filter)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(120, 420)
        self.zoom.setValue(self.thumb_size.width())
        self.zoom.setFixedWidth(110)
        self.zoom.setToolTip("Ukuran thumbnail")
        self.zoom.valueChanged.connect(self._resize_thumbs)

        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.IconMode)
        self.gallery.setResizeMode(QListWidget.Adjust)
        self.gallery.setMovement(QListWidget.Static)
        self.gallery.setUniformItemSizes(True)
        self.gallery.setSpacing(4)
        self.gallery.setWordWrap(True)
        self.gallery.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.gallery.setIconSize(self.thumb_size)
        self.gallery.currentItemChanged.connect(self._on_current)
        self.gallery.itemDoubleClicked.connect(lambda *_: self.replace_selected())
        self.gallery.setContextMenuPolicy(Qt.CustomContextMenu)
        self.gallery.customContextMenuRequested.connect(self._context_menu)

        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("muted")
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.type_filter)
        bar.addWidget(QLabel("🔍"))
        bar.addWidget(self.zoom)

        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        wl.addStretch()
        big = QLabel("⚽")
        big.setAlignment(Qt.AlignCenter)
        big.setStyleSheet("font-size: 64pt;")
        t = QLabel("PES 2021 Adboard Studio")
        t.setObjectName("title")
        t.setAlignment(Qt.AlignCenter)
        sub = QLabel("Buka / tarik folder adboard (berisi .usm, .dds, .png …) ke jendela ini.\n"
                     "Preview video LED, simulasi stadion, ganti dengan gambar/video, "
                     "buat teks LED, backup otomatis.")
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignCenter)
        b = QPushButton("📂  Buka Folder Adboard")
        b.setObjectName("primary")
        b.clicked.connect(self.choose_folder)
        wl.addWidget(big)
        wl.addWidget(t)
        wl.addWidget(sub)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(b)
        row.addStretch()
        wl.addLayout(row)
        wl.addStretch()

        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(welcome)
        self.center_stack.addWidget(self.gallery)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(4, 8, 4, 8)
        cl.addLayout(bar)
        cl.addWidget(self.center_stack, 1)
        cl.addWidget(self.count_lbl)

        # ---- kanan: preview
        self.player = PlayerPanel()
        self.stadium = StadiumPanel()
        self.player.frameChanged.connect(self._frame_changed)
        self.info = QTableWidget(0, 2)
        self.info.horizontalHeader().setVisible(False)
        self.info.verticalHeader().setVisible(False)
        self.info.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.info.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.info.setAlternatingRowColors(True)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.player, "🎬 Preview")
        self.tabs.addTab(self.stadium, "🏟 Simulasi Stadion")
        self.tabs.addTab(self.info, "ℹ Info")
        self.title_lbl = QLabel("Tidak ada file dipilih")
        self.title_lbl.setObjectName("title")
        self.path_lbl = QLabel("")
        self.path_lbl.setObjectName("muted")
        self.path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        quick = QHBoxLayout()
        for text, slot, primary in (("⚡ Ganti", self.replace_selected, True),
                                    ("💡 Teks LED", self.led_generator, False),
                                    ("📤 Export", self.export_selected, False),
                                    ("📁 Buka lokasi", self.reveal_selected, False)):
            btn = QPushButton(text)
            if primary:
                btn.setObjectName("primary")
            btn.clicked.connect(slot)
            quick.addWidget(btn)
        quick.addStretch()
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 8, 8, 8)
        rl.addWidget(self.title_lbl)
        rl.addWidget(self.path_lbl)
        rl.addWidget(self.tabs, 1)
        rl.addLayout(quick)
        drop_hint = QLabel("Tip: tarik gambar/video ke panel preview untuk langsung mengganti "
                           "adboard terpilih.")
        drop_hint.setObjectName("muted")
        rl.addWidget(drop_hint)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setSizes([220, 560, 660])
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 1)
        central = QWidget()
        central.setObjectName("central")
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.setCentralWidget(central)

    # -------------------------------------------------------------- folders
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pilih folder adboard",
                                             str(self.root or Path.home()))
        if d:
            self.open_folder(d)

    def open_folder(self, folder: str) -> None:
        self.root = Path(folder)
        self.settings.setValue("last_folder", folder)
        recent = [r for r in (self.settings.value("recent", []) or []) if r != folder]
        self.settings.setValue("recent", [folder, *recent][:8])
        self._update_recent()
        self.refresh()

    def _update_recent(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.value("recent", []) or []
        if isinstance(recent, str):
            recent = [recent]
        for r in recent:
            self.recent_menu.addAction(r, lambda r=r: self.open_folder(r))
        if not recent:
            self.recent_menu.addAction("(kosong)").setEnabled(False)

    def refresh(self) -> None:
        if not self.root:
            return
        self.statusBar().showMessage(f"Memindai {self.root} …")
        root = self.root
        self._gen += 1
        gen = self._gen
        run_async(lambda: library.scan_folder(root), lambda items: self._scanned(items, gen),
                  lambda e: QMessageBox.critical(self, "Gagal memindai", e))

    def _scanned(self, items: list[library.Item], gen: int) -> None:
        if gen != self._gen:
            return
        self.items = items
        self._build_tree()
        self.center_stack.setCurrentIndex(1)
        self.apply_filter()
        n_usm = sum(i.kind == library.KIND_USM for i in items)
        self.statusBar().showMessage(
            f"{len(items)} file • {n_usm} USM • {len(items) - n_usm} tekstur/lainnya — {self.root}")
        for it in items:
            self._load_thumb(it, gen)

    def _build_tree(self) -> None:
        self.tree.clear()
        root_item = QTreeWidgetItem([f"📁 {self.root.name}"])
        root_item.setData(0, Qt.UserRole, "")
        self.tree.addTopLevelItem(root_item)
        nodes = {"": root_item}
        counts: dict[str, int] = {}
        for it in self.items:
            rel = it.path.parent.relative_to(self.root).as_posix()
            rel = "" if rel == "." else rel
            parts = rel.split("/") if rel else []
            for i in range(len(parts) + 1):
                key = "/".join(parts[:i])
                counts[key] = counts.get(key, 0) + 1
                if key not in nodes:
                    parent = nodes["/".join(parts[:i - 1])]
                    node = QTreeWidgetItem([f"📁 {parts[i - 1]}"])
                    node.setData(0, Qt.UserRole, key)
                    parent.addChild(node)
                    nodes[key] = node
        for key, node in nodes.items():
            name = node.text(0).split("  (")[0]
            node.setText(0, f"{name}  ({counts.get(key, 0)})")
        root_item.setExpanded(True)
        self.tree.setCurrentItem(root_item)

    # -------------------------------------------------------------- gallery
    def apply_filter(self, *_) -> None:
        text = self.search.text().lower().strip()
        kind = self.type_filter.currentData()
        node = self.tree.currentItem()
        sub = node.data(0, Qt.UserRole) if node else ""
        selected = self.current.path if self.current else None
        self.gallery.clear()
        shown = 0
        for it in self.items:
            if kind and it.kind != kind:
                continue
            if text and text not in it.name.lower():
                continue
            if sub:
                rel = it.path.relative_to(self.root).as_posix()
                if not rel.startswith(sub + "/"):
                    continue
            li = QListWidgetItem(it.name)
            li.setData(Qt.UserRole, it)
            li.setToolTip(str(it.path))
            li.setIcon(QIcon(badge_pixmap(self._thumbs.get(str(it.path)), it.kind,
                                          it.path.suffix, self.thumb_size)))
            li.setSizeHint(QSize(self.thumb_size.width() + 16, self.thumb_size.height() + 44))
            self.gallery.addItem(li)
            shown += 1
            if selected and it.path == selected:
                self.gallery.setCurrentItem(li)
        self.count_lbl.setText(f"Menampilkan {shown} dari {len(self.items)} file")

    def _resize_thumbs(self, w: int) -> None:
        self.thumb_size = QSize(w, int(w * 96 / 232))
        self.gallery.setIconSize(self.thumb_size)
        self.apply_filter()

    def _load_thumb(self, item: library.Item, gen: int) -> None:
        key = str(item.path)

        def done(img):
            if gen != self._gen:
                return
            q = pil_to_qimage(img) if img is not None else QImage()
            self._thumbs[key] = q
            self._set_item_icon(key)

        def fail(_):
            if gen == self._gen:
                self._thumbs[key] = QImage()
                self._set_item_icon(key)

        run_async(lambda: library.thumbnail(item, (480, 200)), done, fail)

    def _set_item_icon(self, key: str) -> None:
        for i in range(self.gallery.count()):
            li = self.gallery.item(i)
            it: library.Item = li.data(Qt.UserRole)
            if str(it.path) == key:
                li.setIcon(QIcon(badge_pixmap(self._thumbs.get(key), it.kind, it.path.suffix,
                                              self.thumb_size)))
                break

    def selected_items(self) -> list[library.Item]:
        items = [li.data(Qt.UserRole) for li in self.gallery.selectedItems()]
        if not items and self.current:
            items = [self.current]
        return items

    def _on_current(self, cur: QListWidgetItem | None, _prev=None) -> None:
        if cur is None:
            return
        item: library.Item = cur.data(Qt.UserRole)
        if self.current and self.current.path == item.path and self.player.preview:
            return
        self.current = item
        self._show_item(item)

    def _show_item(self, item: library.Item) -> None:
        self.title_lbl.setText(item.name)
        self.path_lbl.setText(str(item.path))
        self.player.clear("⏳ Memuat preview…")
        self.stadium.set_image(None)
        self.info.setRowCount(0)
        self._preview_gen += 1
        gen = self._preview_gen

        def info_done(info: dict):
            if gen != self._preview_gen:
                return
            self.info.setRowCount(len(info))
            for r, (k, v) in enumerate(info.items()):
                ki = QTableWidgetItem(k)
                ki.setForeground(QColor(theme.MUTED))
                self.info.setItem(r, 0, ki)
                self.info.setItem(r, 1, QTableWidgetItem(str(v)))
            self.info.resizeColumnToContents(0)

        def pv_done(pv):
            if gen == self._preview_gen:
                self.player.set_preview(pv)

        def pv_fail(e):
            if gen == self._preview_gen:
                self.player.clear(f"⚠ {e}")

        run_async(lambda: library.describe(item), info_done,
                  lambda e: info_done({"Error": e}))
        run_async(lambda: library.load_preview(item), pv_done, pv_fail)

    def _frame_changed(self, img: QImage) -> None:
        if self.tabs.currentWidget() is self.stadium or self.stadium.canvas.image is None:
            self.stadium.set_image(img)

    def _context_menu(self, pos) -> None:
        if not self.gallery.itemAt(pos):
            return
        m = QMenu(self)
        m.addAction("⚡ Ganti adboard…", self.replace_selected)
        m.addAction("💡 Ganti dengan teks LED…", self.led_generator)
        m.addAction("📤 Export…", self.export_selected)
        m.addAction("🕘 Backup / restore…", self.backup_selected)
        m.addSeparator()
        m.addAction("📁 Buka lokasi file", self.reveal_selected)
        m.addAction("📋 Salin path", lambda: QApplication.clipboard().setText(
            "\n".join(str(i.path) for i in self.selected_items())))
        m.exec(self.gallery.mapToGlobal(pos))

    # -------------------------------------------------------------- actions
    def _need_selection(self) -> list[library.Item]:
        items = self.selected_items()
        if not items:
            QMessageBox.information(self, "Pilih file", "Pilih dulu adboard di galeri.")
        return items

    def _after_change(self) -> None:
        for it in self.selected_items():
            self._thumbs.pop(str(it.path), None)
        cur = self.current
        self.current = None
        self.refresh()
        if cur:
            self.current = cur
            self._show_item(cur)

    def replace_selected(self, source: str | None = None) -> None:
        items = self._need_selection()
        if not items:
            return
        items = [i for i in items if i.kind != library.KIND_FTEX]
        if not items:
            QMessageBox.warning(self, "FTEX", "File FTEX belum didukung. Konversi dulu ke DDS.")
            return
        dlg = ReplaceDialog(items, self.root, source if isinstance(source, str) else None, self)
        dlg.replaced.connect(self._after_change)
        dlg.exec()

    def led_generator(self) -> None:
        items = self.selected_items()
        dlg = LedTextDialog(items[0] if items else None, self.root, self)
        dlg.created.connect(self._after_change)
        dlg.exec()

    def export_selected(self) -> None:
        items = self._need_selection()
        if not items:
            return
        if len(items) > 1:
            out_dir = QFileDialog.getExistingDirectory(self, "Folder tujuan export")
            if not out_dir:
                return
            fmt, ok = _ask_choice(self, "Format export",
                                  ["MP4 (video) / PNG (gambar)", "PNG frame pertama",
                                   "Elementary stream mentah"])
            if not ok:
                return
            for it in items:
                if it.kind == library.KIND_USM:
                    ext = {0: ".mp4", 1: ".png", 2: ".bin"}[fmt]
                else:
                    ext = ".png"
                self._export(it, Path(out_dir) / (it.path.stem + ext))
            return
        it = items[0]
        if it.kind == library.KIND_USM:
            filt = ("MP4 video (*.mp4);;PNG frame pertama (*.png);;"
                    "Elementary stream (*.m2v *.264 *.ivf *.bin);;Semua frame PNG (folder) (*)")
            path, chosen = QFileDialog.getSaveFileName(self, "Export USM",
                                                       str(it.path.with_suffix(".mp4")), filt)
            if not path:
                return
            if chosen.startswith("Semua frame"):
                path = str(Path(path).with_suffix(""))
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export tekstur", str(it.path.with_suffix(".png")),
                "PNG (*.png);;DDS DXT5 (*.dds);;TGA (*.tga);;JPG (*.jpg)")
            if not path:
                return
        self._export(it, Path(path))

    def _export(self, it: library.Item, out: Path) -> None:
        self.statusBar().showMessage(f"⏳ Export {it.name} → {out.name} …")
        run_async(lambda: library.export_item(it, out),
                  lambda _: self.statusBar().showMessage(f"✅ Export selesai: {out}"),
                  lambda e: QMessageBox.critical(self, "Export gagal", e))

    def backup_selected(self) -> None:
        items = self._need_selection()
        if not items:
            return
        dlg = BackupDialog(items[0], self.root, self)
        dlg.restored.connect(self._after_change)
        dlg.exec()

    def reveal_selected(self) -> None:
        items = self.selected_items()
        if not items:
            return
        p = items[0].path
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def about(self) -> None:
        try:
            ff = ffmpeg_exe()
        except FfmpegError as e:
            ff = str(e)
        QMessageBox.about(
            self, "Tentang",
            f"<h2>PES 2021 Adboard Studio {__version__}</h2>"
            "<p>Editor adboard PES 2021: video LED <b>.usm</b> (CRI Sofdec2) dan tekstur "
            "<b>.dds</b>/gambar.</p>"
            "<ul><li>Preview & player USM (MPEG-1/2, H.264, VP9)</li>"
            "<li>Simulasi tampilan di stadion</li>"
            "<li>Ganti adboard dari gambar/video/USM lain — resolusi, fps & codec otomatis "
            "mengikuti file asli</li>"
            "<li>Generator teks LED berjalan</li>"
            "<li>Backup & restore otomatis</li></ul>"
            f"<p style='color:{theme.MUTED}'>ffmpeg: {ff}</p>")

    # ------------------------------------------------------------ drag/drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if os.path.isdir(path):
            self.open_folder(path)
        elif self.current and self.right_contains(e.position().toPoint()):
            self.replace_selected(path)
        elif os.path.isfile(path):
            self.open_folder(str(Path(path).parent))

    def right_contains(self, pos) -> bool:
        w = self.childAt(pos)
        while w is not None:
            if w is self.tabs:
                return True
            w = w.parentWidget()
        return False


def _ask_choice(parent, title: str, options: list[str]) -> tuple[int, bool]:
    from PySide6.QtWidgets import QInputDialog
    choice, ok = QInputDialog.getItem(parent, title, "Pilih format:", options, 0, False)
    return (options.index(choice) if ok else -1), ok
