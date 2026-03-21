#!/usr/bin/env python3
"""
Tako Reader (タコReader) - Japanese Learning Edition
Supports CBZ, PDF, and image files with Japanese OCR
"""

import sys
import os
import zipfile
import json
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote as url_quote

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QScrollArea, QSlider,
    QStatusBar, QMenuBar, QMenu, QToolBar, QSplitter,
    QTextEdit, QFrame, QComboBox, QSpinBox, QDialog,
    QDialogButtonBox, QCheckBox, QGroupBox, QListWidget,
    QListWidgetItem, QSizePolicy, QRubberBand, QMessageBox,
    QProgressDialog, QLineEdit
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QPoint, QThread, pyqtSignal, QTimer,
    QSettings, QRectF, QPointF
)
from PyQt6.QtGui import (
    QPixmap, QImage, QKeySequence, QAction, QFont, QColor,
    QPainter, QPen, QBrush, QCursor, QIcon, QPalette, QGuiApplication
)


# ─── OCR Worker Thread ──────────────────────────────────────────────────────

class OCRWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, image: QImage, rect: QRect):
        super().__init__()
        self.image = image
        self.rect = rect

    def run(self):
        try:
            import manga_ocr
        except ImportError:
            self.error_occurred.emit("manga-ocr not installed.\nRun: pip install manga-ocr")
            return

        try:
            from PIL import Image as PILImage
            import numpy as np

            # Crop to selection
            cropped = self.image.copy(self.rect)
            w, h = cropped.width(), cropped.height()
            ptr = cropped.bits()
            ptr.setsize(h * w * 4)
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
            pil_img = PILImage.fromarray(arr[:, :, :3])  # drop alpha

            # Run OCR (model loads once, cached globally)
            if not hasattr(OCRWorker, '_model'):
                OCRWorker._model = manga_ocr.MangaOcr()
            text = OCRWorker._model(pil_img)
            self.result_ready.emit(text)
        except Exception as e:
            self.error_occurred.emit(str(e))


# ─── Page View ──────────────────────────────────────────────────────────────

class PageView(QLabel):
    """Displays a single manga page with zoom, pan, and OCR selection."""

    ocr_requested = pyqtSignal(QImage, QRect)  # full page image + selection rect

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #1a1a1a;")
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._pixmap_orig: QPixmap | None = None
        self._scale = 1.0
        self._fit_mode = "fit_width"  # fit_width | fit_page | custom
        self._ocr_mode = False

        # Rubber-band selection (OCR)
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._sel_origin = QPoint()

        # Pan state (Shift + drag)
        self._panning = False
        self._pan_start: QPoint = QPoint()
        self._scroll_start: QPoint = QPoint()  # scroll bar values at pan start

    # ── Public API ──

    def set_pixmap(self, px: QPixmap):
        self._pixmap_orig = px
        self._apply_fit()

    def set_scale(self, scale: float):
        self._scale = max(0.1, min(scale, 8.0))
        self._fit_mode = "custom"
        self._render()

    def set_fit_mode(self, mode: str):
        self._fit_mode = mode
        self._apply_fit()

    def set_ocr_mode(self, enabled: bool):
        self._ocr_mode = enabled
        self._update_cursor()

    def _update_cursor(self):
        if self._panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._ocr_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ── Internal ──

    def _apply_fit(self):
        if not self._pixmap_orig:
            return
        pw, ph = self._pixmap_orig.width(), self._pixmap_orig.height()
        vw, vh = self.width(), self.height()
        if self._fit_mode == "fit_width":
            self._scale = vw / pw if pw else 1.0
        elif self._fit_mode == "fit_page":
            self._scale = min(vw / pw, vh / ph) if pw and ph else 1.0
        self._render()

    def _render(self):
        if not self._pixmap_orig:
            return
        w = int(self._pixmap_orig.width() * self._scale)
        h = int(self._pixmap_orig.height() * self._scale)
        scaled = self._pixmap_orig.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode != "custom":
            self._apply_fit()

    # ── Mouse: pan (Shift+drag) and rubber-band OCR ──

    def _scroll_area(self):
        """Walk up to the parent QScrollArea, if any."""
        p = self.parent()
        while p:
            if isinstance(p, QScrollArea):
                return p
            p = p.parent()
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if shift:
                # Start pan
                self._panning = True
                self._pan_start = event.globalPosition().toPoint()
                sa = self._scroll_area()
                if sa:
                    self._scroll_start = QPoint(
                        sa.horizontalScrollBar().value(),
                        sa.verticalScrollBar().value()
                    )
                self._update_cursor()
                event.accept()
                return
            if self._ocr_mode:
                self._sel_origin = event.pos()
                self._rubber_band.setGeometry(QRect(self._sel_origin, QSize()))
                self._rubber_band.show()

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.globalPosition().toPoint() - self._pan_start
            sa = self._scroll_area()
            if sa:
                sa.horizontalScrollBar().setValue(self._scroll_start.x() - delta.x())
                sa.verticalScrollBar().setValue(self._scroll_start.y() - delta.y())
            event.accept()
            return
        if self._ocr_mode and not self._sel_origin.isNull():
            self._rubber_band.setGeometry(
                QRect(self._sel_origin, event.pos()).normalized()
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._panning:
                self._panning = False
                self._update_cursor()
                event.accept()
                return
            if self._ocr_mode:
                self._rubber_band.hide()
                sel = QRect(self._sel_origin, event.pos()).normalized()
                self._sel_origin = QPoint()
                if sel.width() > 5 and sel.height() > 5 and self._pixmap_orig:
                    pm = self.pixmap()
                    if pm:
                        offset_x = (self.width() - pm.width()) // 2
                        offset_y = (self.height() - pm.height()) // 2
                        img_x = int((sel.x() - offset_x) / self._scale)
                        img_y = int((sel.y() - offset_y) / self._scale)
                        img_w = int(sel.width() / self._scale)
                        img_h = int(sel.height() / self._scale)
                        img_rect = QRect(img_x, img_y, img_w, img_h).intersected(
                            QRect(0, 0, self._pixmap_orig.width(), self._pixmap_orig.height())
                        )
                        if img_rect.isValid():
                            full_image = self._pixmap_orig.toImage()
                            self.ocr_requested.emit(full_image, img_rect)


# ─── OCR Sidebar ────────────────────────────────────────────────────────────

class OCRPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("📖 OCR / Text")
        title.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        layout.addWidget(title)

        self.text_box = QTextEdit()
        self.text_box.setReadOnly(False)
        self.text_box.setFont(QFont("Noto Serif JP, serif", 16))
        self.text_box.setPlaceholderText("Select text area on page\nto run OCR…")
        self.text_box.setStyleSheet("""
            QTextEdit {
                background: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 8px;
                font-size: 18px;
            }
        """)
        # Custom context menu
        self.text_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_box.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.text_box, stretch=1)

        # ── Action buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.text_box.clear)
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

        # ── Jisho button ──
        self.jisho_btn = QPushButton("🔍  Search Jisho")
        self.jisho_btn.setToolTip(
            "Search selected text on Jisho.org\n(uses all text if nothing is selected)"
        )
        self.jisho_btn.setStyleSheet("""
            QPushButton {
                background: #2a6496;
                color: #fff;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover { background: #3a7abf; }
            QPushButton:pressed { background: #1e4f75; }
        """)
        self.jisho_btn.clicked.connect(self._search_jisho)
        layout.addWidget(self.jisho_btn)

        # ── Takoboto button ──
        self.takoboto_btn = QPushButton("🐙  Search Takoboto")
        self.takoboto_btn.setToolTip(
            "Search selected text on Takoboto.jp\n(uses all text if nothing is selected)"
        )
        self.takoboto_btn.setStyleSheet("""
            QPushButton {
                background: #3d6b4f;
                color: #fff;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover { background: #4e8a65; }
            QPushButton:pressed { background: #2b4d38; }
        """)
        self.takoboto_btn.clicked.connect(self._search_takoboto)
        layout.addWidget(self.takoboto_btn)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # ── Helpers ──

    def _selected_or_all(self) -> str:
        """Return highlighted text, falling back to the full box contents."""
        cursor = self.text_box.textCursor()
        text = cursor.selectedText().strip()
        if not text:
            text = self.text_box.toPlainText().strip()
        return text

    # ── Slots ──

    def set_text(self, text: str):
        current = self.text_box.toPlainText()
        if current:
            self.text_box.setPlainText(current + "\n" + text)
        else:
            self.text_box.setPlainText(text)
        self.status.setText("✓ OCR complete")

    def set_status(self, msg: str):
        self.status.setText(msg)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.text_box.toPlainText())
        self.status.setText("Copied!")

    def _search_jisho(self):
        text = self._selected_or_all()
        if not text:
            self.status.setText("Nothing to search.")
            return
        url = "https://jisho.org/search/" + url_quote(text)
        webbrowser.open(url)
        self.status.setText("Opened in browser ↗")

    def _search_takoboto(self):
        text = self._selected_or_all()
        if not text:
            self.status.setText("Nothing to search.")
            return
        url = "https://takoboto.jp/?q=" + url_quote(text)
        webbrowser.open(url)
        self.status.setText("Opened in browser ↗")

    def _show_context_menu(self, pos):
        menu = self.text_box.createStandardContextMenu()
        menu.addSeparator()
        has_text = bool(self.text_box.toPlainText().strip())
        jisho_act = QAction("🔍  Search Jisho", self)
        jisho_act.triggered.connect(self._search_jisho)
        jisho_act.setEnabled(has_text)
        menu.addAction(jisho_act)
        takoboto_act = QAction("🐙  Search Takoboto", self)
        takoboto_act.triggered.connect(self._search_takoboto)
        takoboto_act.setEnabled(has_text)
        menu.addAction(takoboto_act)
        menu.exec(self.text_box.viewport().mapToGlobal(pos))


# ─── Thumbnail Strip ─────────────────────────────────────────────────────────

class ThumbnailList(QListWidget):
    page_selected = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(110)
        self.setIconSize(QSize(90, 120))
        self.setSpacing(4)
        self.setStyleSheet("""
            QListWidget { background: #121212; border: none; }
            QListWidget::item { border-radius: 4px; }
            QListWidget::item:selected { background: #3584e4; }
        """)
        self.itemClicked.connect(lambda item: self.page_selected.emit(self.row(item)))

    def load_pages(self, pixmaps: list[QPixmap]):
        self.clear()
        for i, px in enumerate(pixmaps):
            thumb = px.scaled(90, 120, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            item = QListWidgetItem(QIcon(thumb), f"  {i+1}")
            self.addItem(item)

    def select_page(self, index: int):
        self.setCurrentRow(index)


# ─── File Loader ─────────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}

def load_pages_from_path(path: str) -> list[QPixmap]:
    """Return ordered list of QPixmaps from CBZ, PDF, or image file."""
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".cbz" or ext == ".zip":
        return _load_cbz(path)
    elif ext == ".pdf":
        return _load_pdf(path)
    elif ext in IMAGE_EXTS:
        px = QPixmap(path)
        return [px] if not px.isNull() else []
    else:
        # Try as directory
        if p.is_dir():
            return _load_dir(path)
        raise ValueError(f"Unsupported format: {ext}")

def _load_cbz(path: str) -> list[QPixmap]:
    pages = []
    with zipfile.ZipFile(path, "r") as zf:
        names = sorted([
            n for n in zf.namelist()
            if Path(n).suffix.lower() in IMAGE_EXTS and not n.startswith("__")
        ])
        for name in names:
            data = zf.read(name)
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                pages.append(QPixmap.fromImage(img))
    return pages

def _load_pdf(path: str) -> list[QPixmap]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("PyMuPDF not installed.\nRun: pip install pymupdf")

    pages = []
    doc = fitz.open(path)
    for page in doc:
        mat = fitz.Matrix(2.0, 2.0)  # 2x scale → ~144 DPI
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        pages.append(QPixmap.fromImage(img.copy()))
    doc.close()
    return pages

def _load_dir(path: str) -> list[QPixmap]:
    pages = []
    files = sorted([
        f for f in Path(path).iterdir()
        if f.suffix.lower() in IMAGE_EXTS
    ])
    for f in files:
        px = QPixmap(str(f))
        if not px.isNull():
            pages.append(px)
    return pages


# ─── Main Window ─────────────────────────────────────────────────────────────

class TakoReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tako Reader — タコReader")
        self.resize(1280, 900)

        self._pages: list[QPixmap] = []
        self._current = 0
        self._ocr_worker: OCRWorker | None = None
        self._settings = QSettings("TakoReader", "TakoReaderJP")
        self._reading_mode = "rtl"  # rtl (manga) | ltr

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._apply_dark_theme()
        self._restore_settings()
        self.setAcceptDrops(True)

    # ── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Thumbnail strip (left)
        self.thumb_list = ThumbnailList()
        self.thumb_list.page_selected.connect(self.go_to_page)
        root.addWidget(self.thumb_list)

        # Center: page + nav
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # Scroll area for the page
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: #1a1a1a; }")

        self.page_view = PageView()
        self.page_view.ocr_requested.connect(self._run_ocr)
        self.scroll.setWidget(self.page_view)
        center_layout.addWidget(self.scroll, stretch=1)

        # Bottom nav bar
        nav = self._build_nav_bar()
        center_layout.addWidget(nav)

        root.addWidget(center_widget, stretch=1)

        # OCR panel (right)
        self.ocr_panel = OCRPanel()
        root.addWidget(self.ocr_panel)

        self.statusBar().showMessage("Open a file to begin (File → Open)  🐙")

    def _build_nav_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background: #141414; border-top: 1px solid #2a2a2a;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)

        self.btn_first = QPushButton("⏮")
        self.btn_prev = QPushButton("◀  Prev")
        self.btn_next = QPushButton("Next  ▶")
        self.btn_last = QPushButton("⏭")

        for b in (self.btn_first, self.btn_prev, self.btn_next, self.btn_last):
            b.setFixedHeight(32)
            b.setStyleSheet("""
                QPushButton {
                    background: #2a2a2a; color: #ddd; border-radius: 6px;
                    padding: 0 14px; font-size: 10pt;
                }
                QPushButton:hover { background: #3584e4; }
                QPushButton:disabled { color: #555; }
            """)

        self.btn_first.clicked.connect(lambda: self.go_to_page(0))
        self.btn_prev.clicked.connect(self.prev_page)
        self.btn_next.clicked.connect(self.next_page)
        self.btn_last.clicked.connect(lambda: self.go_to_page(len(self._pages) - 1))

        self.page_label = QLabel("— / —")
        self.page_label.setStyleSheet("color: #aaa; font-size: 10pt;")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setFixedWidth(90)

        self.page_spin = QSpinBox()
        self.page_spin.setFixedWidth(60)
        self.page_spin.setStyleSheet("""
            QSpinBox { background: #2a2a2a; color: #ddd; border: 1px solid #444;
                       border-radius: 4px; padding: 2px 4px; }
        """)
        self.page_spin.valueChanged.connect(lambda v: self.go_to_page(v - 1))

        lay.addWidget(self.btn_first)
        lay.addWidget(self.btn_prev)
        lay.addStretch()
        lay.addWidget(self.page_spin)
        lay.addWidget(self.page_label)
        lay.addStretch()
        lay.addWidget(self.btn_next)
        lay.addWidget(self.btn_last)
        return bar

    def _build_menu(self):
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("File")
        open_act = QAction("Open…", self, shortcut="Ctrl+O")
        open_act.triggered.connect(self.open_file)
        open_dir_act = QAction("Open Folder…", self)
        open_dir_act.triggered.connect(self.open_folder)
        quit_act = QAction("Quit", self, shortcut="Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addActions([open_act, open_dir_act])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        # View
        view_menu = mb.addMenu("View")
        fit_w = QAction("Fit Width", self, shortcut="W")
        fit_w.triggered.connect(lambda: self.page_view.set_fit_mode("fit_width"))
        fit_p = QAction("Fit Page", self, shortcut="F")
        fit_p.triggered.connect(lambda: self.page_view.set_fit_mode("fit_page"))
        zoom_in = QAction("Zoom In", self, shortcut="Ctrl+=")
        zoom_in.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale * 1.2))
        zoom_out = QAction("Zoom Out", self, shortcut="Ctrl+-")
        zoom_out.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale / 1.2))

        self.act_thumbnails = QAction("Show Thumbnails", self, checkable=True, checked=True)
        self.act_thumbnails.triggered.connect(
            lambda v: self.thumb_list.setVisible(v)
        )
        self.act_ocr_panel = QAction("Show OCR Panel", self, checkable=True, checked=True)
        self.act_ocr_panel.triggered.connect(
            lambda v: self.ocr_panel.setVisible(v)
        )

        rtl_act = QAction("RTL (Manga)", self, checkable=True, checked=True)
        rtl_act.triggered.connect(lambda v: self._set_reading_mode("rtl" if v else "ltr"))

        view_menu.addActions([fit_w, fit_p, zoom_in, zoom_out])
        view_menu.addSeparator()
        view_menu.addActions([self.act_thumbnails, self.act_ocr_panel])
        view_menu.addSeparator()
        view_menu.addAction(rtl_act)

        # Navigate
        nav_menu = mb.addMenu("Navigate")
        prev_a = QAction("Previous Page", self, shortcut="Left")
        prev_a.triggered.connect(self.prev_page)
        next_a = QAction("Next Page", self, shortcut="Right")
        next_a.triggered.connect(self.next_page)
        nav_menu.addActions([prev_a, next_a])

        # OCR
        ocr_menu = mb.addMenu("OCR")
        self.act_ocr_mode = QAction("OCR Selection Mode", self,
                                     shortcut="Ctrl+Shift+O", checkable=True)
        self.act_ocr_mode.triggered.connect(self._toggle_ocr_mode)
        ocr_menu.addAction(self.act_ocr_mode)

        install_ocr = QAction("Check OCR Installation…", self)
        install_ocr.triggered.connect(self._check_ocr)
        ocr_menu.addAction(install_ocr)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setStyleSheet("""
            QToolBar { background: #1e1e1e; border-bottom: 1px solid #2a2a2a; spacing: 4px; padding: 2px; }
            QToolButton { background: transparent; color: #ccc; border-radius: 4px;
                          padding: 4px 10px; font-size: 13pt; }
            QToolButton:hover { background: #2e2e2e; }
            QToolButton:checked { background: #3584e4; color: white; }
        """)

        open_btn = QAction("📂 Open", self)
        open_btn.triggered.connect(self.open_file)
        tb.addAction(open_btn)
        tb.addSeparator()

        fit_w_btn = QAction("↔ Fit Width", self)
        fit_w_btn.triggered.connect(lambda: self.page_view.set_fit_mode("fit_width"))
        fit_p_btn = QAction("⬜ Fit Page", self)
        fit_p_btn.triggered.connect(lambda: self.page_view.set_fit_mode("fit_page"))
        tb.addActions([fit_w_btn, fit_p_btn])
        tb.addSeparator()

        zi = QAction("🔍+", self)
        zi.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale * 1.2))
        zo = QAction("🔍−", self)
        zo.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale / 1.2))
        tb.addActions([zi, zo])
        tb.addSeparator()

        self.ocr_btn = QAction("🔤 OCR Mode", self, checkable=True)
        self.ocr_btn.triggered.connect(self._toggle_ocr_mode)
        tb.addAction(self.ocr_btn)

    # ── Drag & Drop ──────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._load_path(path)
        event.acceptProposedAction()

    # ── File Loading ─────────────────────────────────────────────────────────

    def open_file(self):
        last_dir = self._settings.value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Manga File", last_dir,
            "Manga Files (*.cbz *.zip *.pdf *.jpg *.jpeg *.png *.webp *.bmp);;All Files (*)"
        )
        if path:
            self._load_path(path)

    def open_folder(self):
        last_dir = self._settings.value("last_dir", "")
        path = QFileDialog.getExistingDirectory(self, "Open Manga Folder", last_dir)
        if path:
            self._load_path(path)

    def _load_path(self, path: str):
        prog = QProgressDialog("Loading pages…", None, 0, 0, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(300)
        prog.setValue(0)
        QApplication.processEvents()

        try:
            pages = load_pages_from_path(path)
        except Exception as e:
            prog.close()
            QMessageBox.critical(self, "Load Error", str(e))
            return

        prog.close()

        if not pages:
            QMessageBox.warning(self, "No Pages", "No readable images found in this file.")
            return

        self._pages = pages
        self._current = 0
        self._settings.setValue("last_dir", str(Path(path).parent))
        self.setWindowTitle(f"Tako Reader — {Path(path).name}")

        self.thumb_list.load_pages(pages)
        self.page_spin.setRange(1, len(pages))

        self.go_to_page(0)
        self.statusBar().showMessage(f"Loaded {len(pages)} pages — {Path(path).name}")

    # ── Navigation ───────────────────────────────────────────────────────────

    def go_to_page(self, index: int):
        if not self._pages:
            return
        index = max(0, min(index, len(self._pages) - 1))
        self._current = index
        self.page_view.set_pixmap(self._pages[index])
        self.thumb_list.select_page(index)
        self.page_label.setText(f"{index+1} / {len(self._pages)}")
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(index + 1)
        self.page_spin.blockSignals(False)
        self.btn_prev.setEnabled(index > 0)
        self.btn_next.setEnabled(index < len(self._pages) - 1)
        self.btn_first.setEnabled(index > 0)
        self.btn_last.setEnabled(index < len(self._pages) - 1)

    def prev_page(self):
        if self._reading_mode == "rtl":
            self.go_to_page(self._current + 1)
        else:
            self.go_to_page(self._current - 1)

    def next_page(self):
        if self._reading_mode == "rtl":
            self.go_to_page(self._current - 1)
        else:
            self.go_to_page(self._current + 1)

    def _set_reading_mode(self, mode: str):
        self._reading_mode = mode
        self.statusBar().showMessage(f"Reading mode: {'Right→Left (Manga)' if mode=='rtl' else 'Left→Right'}")

    # ── OCR ──────────────────────────────────────────────────────────────────

    def _toggle_ocr_mode(self, checked: bool | None = None):
        if checked is None:
            checked = not self.act_ocr_mode.isChecked()
        self.act_ocr_mode.setChecked(checked)
        self.ocr_btn.setChecked(checked)
        self.page_view.set_ocr_mode(checked)
        if checked:
            self.statusBar().showMessage("OCR mode: drag to select text region on page")
        else:
            self.statusBar().showMessage("OCR mode off")

    def _run_ocr(self, image: QImage, rect: QRect):
        if self._ocr_worker and self._ocr_worker.isRunning():
            return
        self.ocr_panel.set_status("⏳ Running OCR…")
        self._ocr_worker = OCRWorker(image, rect)
        self._ocr_worker.result_ready.connect(self.ocr_panel.set_text)
        self._ocr_worker.error_occurred.connect(self.ocr_panel.set_status)
        self._ocr_worker.start()

    def _check_ocr(self):
        try:
            import manga_ocr
            msg = "✅ manga-ocr is installed and ready."
        except ImportError:
            msg = ("❌ manga-ocr is NOT installed.\n\n"
                   "To install, run:\n"
                   "  pip install manga-ocr\n\n"
                   "This will download the OCR model (~400 MB) on first use.")
        QMessageBox.information(self, "OCR Status", msg)

    # ── Keyboard ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_N):
            self.next_page()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_B, Qt.Key.Key_P):
            self.prev_page()
        elif key == Qt.Key.Key_Home:
            self.go_to_page(0)
        elif key == Qt.Key.Key_End:
            self.go_to_page(len(self._pages) - 1)
        elif key == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        else:
            super().keyPressEvent(event)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #1a1a1a; color: #e0e0e0; }
            QMenuBar { background: #1e1e1e; color: #ddd; border-bottom: 1px solid #2a2a2a; }
            QMenuBar::item:selected { background: #3584e4; }
            QMenu { background: #252525; color: #ddd; border: 1px solid #3a3a3a; }
            QMenu::item:selected { background: #3584e4; }
            QStatusBar { background: #1e1e1e; color: #888; font-size: 9pt; }
            QScrollBar:vertical { background: #1a1a1a; width: 10px; }
            QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 5px; min-height: 30px; }
            QScrollBar:horizontal { background: #1a1a1a; height: 10px; }
            QScrollBar::handle:horizontal { background: #3a3a3a; border-radius: 5px; }
        """)

    # ── Settings ─────────────────────────────────────────────────────────────

    def _restore_settings(self):
        geo = self._settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def closeEvent(self, event):
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    # Must be set before QApplication is created
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("TakoReader")
    app.setOrganizationName("TakoReaderJP")

    window = TakoReader()
    window.show()

    # If a file was passed on the command line
    if len(sys.argv) > 1:
        window._load_path(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
