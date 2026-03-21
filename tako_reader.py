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
import platform
from pathlib import Path
from urllib.parse import quote as url_quote

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QScrollArea,
    QTextEdit, QSpinBox, QListWidget,
    QListWidgetItem, QSizePolicy, QRubberBand, QMessageBox,
    QProgressDialog, QMenuBar, QMenu
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QPoint, QThread, pyqtSignal,
    QSettings, QTimer, QEvent
)
from PyQt6.QtGui import (
    QPixmap, QImage, QAction, QFont, QColor,
    QCursor, QIcon, QGuiApplication, QPainter, QBrush, QPen
)

# platform checks available if needed:
# IS_WINDOWS = platform.system() == "Windows"
# IS_MAC     = platform.system() == "Darwin"

# (Windows Aero Snap / WM_NCHITTEST hook removed — Python 3.14 changed the
#  ctypes MSG pointer ABI in PyQt6's nativeEvent, causing a hard crash on show.
#  Resize is handled via Qt mouse events on all platforms instead.)


# ─── OCR Process Manager ─────────────────────────────────────────────────────
# Keeps a single long-lived subprocess alive so the model loads once.
# The child reads one JSON line per request and writes one JSON line per result,
# so it stays hot between OCR calls.

_OCR_PROCESS_SCRIPT = """
import sys, json, base64, io

def main():
    # Load model once, then loop reading requests from stdin
    device = sys.argv[1] if len(sys.argv) > 1 else "cpu"
    try:
        import manga_ocr
        from PIL import Image as PILImage
        model = manga_ocr.MangaOcr(force_cpu=(device == "cpu"))
        # Signal ready
        print(json.dumps({"ready": True}), flush=True)
    except Exception:
        import traceback
        print(json.dumps({"ready": False, "error": traceback.format_exc()}), flush=True)
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            img_bytes = base64.b64decode(data["image_b64"])
            pil_img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
            text = model(pil_img)
            print(json.dumps({"ok": True, "text": text}), flush=True)
        except Exception:
            import traceback
            print(json.dumps({"ok": False, "error": traceback.format_exc()}), flush=True)

main()
"""


class OCRProcessManager:
    """
    Singleton-per-device that owns a long-lived OCR subprocess.
    The process loads manga_ocr once, then handles unlimited requests.
    """
    _instances: dict = {}

    @classmethod
    def get(cls, device: str) -> "OCRProcessManager":
        if device not in cls._instances:
            cls._instances[device] = cls(device)
        return cls._instances[device]

    @classmethod
    def shutdown_all(cls):
        for mgr in cls._instances.values():
            mgr._stop()
        cls._instances.clear()

    def __init__(self, device: str):
        self.device  = device
        self._proc   = None
        self._ready  = False
        self._error  = None

    def _start(self):
        """Launch the worker process and wait for its ready signal."""
        import subprocess
        self._proc = subprocess.Popen(
            [sys.executable, "-c", _OCR_PROCESS_SCRIPT, self.device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Read the ready line (blocks until model is loaded)
        ready_line = self._proc.stdout.readline().strip()
        if ready_line:
            import json
            data = json.loads(ready_line)
            if data.get("ready"):
                self._ready = True
            else:
                self._error = data.get("error", "Unknown startup error")
                self._stop()
        else:
            stderr = self._proc.stderr.read()
            self._error = stderr or "No ready signal from OCR process"
            self._stop()

    def _stop(self):
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc  = None
            self._ready = False

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def run_ocr(self, img_b64: str) -> dict:
        """Send an image and return the result dict. Blocks until done."""
        import json
        if not self.is_alive():
            self._ready = False
            self._error = None
            self._start()
        if not self._ready:
            return {"ok": False, "error": self._error or "OCR process failed to start"}
        try:
            payload = json.dumps({"image_b64": img_b64}) + "\n"
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
            result_line = self._proc.stdout.readline().strip()
            if not result_line:
                stderr = self._proc.stderr.read()
                self._stop()
                return {"ok": False, "error": stderr or "No response from OCR process"}
            return json.loads(result_line)
        except Exception:
            import traceback
            self._stop()
            return {"ok": False, "error": traceback.format_exc()}


class OCRWorker(QThread):
    """Qt thread that calls OCRProcessManager so the UI never blocks."""
    result_ready   = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, image: QImage, rect: QRect, device: str = "cpu"):
        super().__init__()
        self.image  = image
        self.rect   = rect
        self.device = device

    def run(self):
        try:
            import base64, io
            import numpy as np
            from PIL import Image as PILImage

            # Crop and encode image
            cropped = self.image.copy(self.rect)
            w, h = cropped.width(), cropped.height()
            ptr  = cropped.bits()
            ptr.setsize(h * w * 4)
            arr  = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
            pil  = PILImage.fromarray(arr[:, :, :3])
            buf  = io.BytesIO()
            pil.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            mgr    = OCRProcessManager.get(self.device)
            result = mgr.run_ocr(img_b64)

            if result.get("ok"):
                self.result_ready.emit(result["text"])
            else:
                self.error_occurred.emit("OCR error:\n" + result.get('error', 'unknown'))

        except Exception:
            import traceback
            self.error_occurred.emit(traceback.format_exc())


# ─── Custom Title Bar ─────────────────────────────────────────────────────────

class TitleBar(QWidget):
    """
    Custom frameless title bar: icon, title text, and
    min / max / close buttons.  Double-click toggles maximise.
    Drag moves the window; dragging off a maximised window un-maximises it.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("TitleBar")

        self._drag_pos: QPoint | None = None
        self._window = parent

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 4, 0)
        lay.setSpacing(0)

        # App icon
        icon_lbl = QLabel("🐙")
        icon_lbl.setFixedWidth(26)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon_lbl)
        lay.addSpacing(4)

        # Title
        self._title_lbl = QLabel("Tako Reader — タコReader")
        self._title_lbl.setObjectName("TitleLabel")
        self._title_lbl.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        lay.addWidget(self._title_lbl, stretch=1)

        # Window control buttons
        for attr, text, hover_bg, slot in [
            ("min_btn", "─",  "#3a3a4a", self._on_min),
            ("max_btn", "□",  "#3a3a4a", self._on_max),
            ("clo_btn", "✕",  "#c0392b", self._on_close),
        ]:
            btn = QPushButton(text)
            btn.setFixedSize(40, 36)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: #bbbbbb;
                    border: none;
                    font-size: 14px;
                }}
                QPushButton:hover {{
                    background: {hover_bg};
                    color: #ffffff;
                }}
            """)
            btn.clicked.connect(slot)
            lay.addWidget(btn)
            setattr(self, attr, btn)

    # ── Actions ──

    def _on_min(self):   self._window.showMinimized()
    def _on_close(self): self._window.close()

    def _on_max(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def update_max_icon(self):
        self.max_btn.setText("❐" if self._window.isMaximized() else "□")

    def set_title(self, text: str):
        self._title_lbl.setText(text)

    # ── Drag to move ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            if self._window.isMaximized():
                # Un-maximise, keeping cursor proportional to new width
                ratio = event.globalPosition().toPoint().x() / self._window.width()
                self._window.showNormal()
                new_x = int(event.globalPosition().toPoint().x()
                            - self._window.width() * ratio)
                self._window.move(new_x, 0)
                self._drag_pos = event.globalPosition().toPoint()
            else:
                delta = event.globalPosition().toPoint() - self._drag_pos
                self._window.move(self._window.pos() + delta)
                self._drag_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_max()
        super().mouseDoubleClickEvent(event)


# ─── Page View ───────────────────────────────────────────────────────────────

class PageView(QLabel):
    """Single manga page: zoom, Shift+drag pan, OCR rubber-band selection."""

    ocr_requested = pyqtSignal(QImage, QRect)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #1a1a1a;")

        self._pixmap_orig: QPixmap | None = None
        self._scale    = 1.0
        self._fit_mode = "fit_width"
        self._ocr_mode = False

        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._sel_origin  = QPoint()

        self._panning      = False
        self._pan_start    = QPoint()
        self._scroll_start = QPoint()

    def set_pixmap(self, px: QPixmap):
        self._pixmap_orig = px
        self._apply_fit()

    def set_scale(self, scale: float):
        self._scale    = max(0.1, min(scale, 8.0))
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
        w = int(self._pixmap_orig.width()  * self._scale)
        h = int(self._pixmap_orig.height() * self._scale)
        self.setPixmap(self._pixmap_orig.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode != "custom":
            self._apply_fit()

    def _scroll_area(self):
        p = self.parent()
        while p:
            if isinstance(p, QScrollArea):
                return p
            p = p.parent()
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._panning      = True
                self._pan_start    = event.globalPosition().toPoint()
                sa = self._scroll_area()
                if sa:
                    self._scroll_start = QPoint(
                        sa.horizontalScrollBar().value(),
                        sa.verticalScrollBar().value(),
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
                        offset_x = (self.width()  - pm.width())  // 2
                        offset_y = (self.height() - pm.height()) // 2
                        img_rect = QRect(
                            int((sel.x() - offset_x) / self._scale),
                            int((sel.y() - offset_y) / self._scale),
                            int(sel.width()  / self._scale),
                            int(sel.height() / self._scale),
                        ).intersected(
                            QRect(0, 0, self._pixmap_orig.width(), self._pixmap_orig.height())
                        )
                        if img_rect.isValid():
                            self.ocr_requested.emit(self._pixmap_orig.toImage(), img_rect)


# ─── OCR Sidebar ─────────────────────────────────────────────────────────────

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
        self.text_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_box.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.text_box, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.copy_btn  = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.text_box.clear)
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

        self.jisho_btn = QPushButton("🔍  Search Jisho")
        self.jisho_btn.setToolTip(
            "Search selected text on Jisho.org\n(uses all text if nothing is selected)"
        )
        self.jisho_btn.setStyleSheet("""
            QPushButton {
                background: #2a6496; color: #fff;
                border-radius: 6px; padding: 6px 10px;
                font-size: 10pt; font-weight: bold;
            }
            QPushButton:hover   { background: #3a7abf; }
            QPushButton:pressed { background: #1e4f75; }
        """)
        self.jisho_btn.clicked.connect(self._search_jisho)
        layout.addWidget(self.jisho_btn)

        self.takoboto_btn = QPushButton("🐙  Search Takoboto")
        self.takoboto_btn.setToolTip(
            "Search selected text on Takoboto.jp\n(uses all text if nothing is selected)"
        )
        self.takoboto_btn.setStyleSheet("""
            QPushButton {
                background: #3d6b4f; color: #fff;
                border-radius: 6px; padding: 6px 10px;
                font-size: 10pt; font-weight: bold;
            }
            QPushButton:hover   { background: #4e8a65; }
            QPushButton:pressed { background: #2b4d38; }
        """)
        self.takoboto_btn.clicked.connect(self._search_takoboto)
        layout.addWidget(self.takoboto_btn)

        # ── Device selector ──
        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_lbl = QLabel("OCR device:")
        device_lbl.setStyleSheet("color: #888; font-size: 9pt;")
        device_row.addWidget(device_lbl)

        from PyQt6.QtWidgets import QComboBox
        self.device_combo = QComboBox()
        self.device_combo.setStyleSheet("""
            QComboBox {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 2px 6px; font-size: 9pt;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #252525; color: #ddd;
                selection-background-color: #3584e4;
            }
        """)
        self._populate_devices()
        device_row.addWidget(self.device_combo, stretch=1)

        self.reload_model_btn = QPushButton("↺")
        self.reload_model_btn.setFixedWidth(28)
        self.reload_model_btn.setToolTip("Reload OCR model on selected device")
        self.reload_model_btn.setStyleSheet("""
            QPushButton {
                background: #2a2a2a; color: #aaa;
                border: 1px solid #444; border-radius: 4px; font-size: 11pt;
            }
            QPushButton:hover { background: #3a3a3a; color: #fff; }
        """)
        self.reload_model_btn.clicked.connect(self._reload_model)
        device_row.addWidget(self.reload_model_btn)
        layout.addLayout(device_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _populate_devices(self):
        """Fill the combo with CPU + any available CUDA devices.
        Probes torch in a subprocess so a DLL crash can never kill the app."""
        self.device_combo.clear()
        self.device_combo.addItem("CPU", "cpu")

        # Probe torch/CUDA in a subprocess — if torch DLLs are broken or
        # CUDA isn't available the main process is completely unaffected.
        try:
            import subprocess, json as _json
            probe = (
                "import json, sys\n"
                "try:\n"
                "    import torch\n"
                "    devices = []\n"
                "    if torch.cuda.is_available():\n"
                "        for i in range(torch.cuda.device_count()):\n"
                "            devices.append({'id': i, 'name': torch.cuda.get_device_name(i)})\n"
                "    print(json.dumps({'ok': True, 'devices': devices}))\n"
                "except Exception as e:\n"
                "    print(json.dumps({'ok': False, 'error': str(e)}))\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True, text=True, timeout=15
            )
            if result.stdout.strip():
                data = _json.loads(result.stdout.strip().splitlines()[-1])
                if data.get("ok"):
                    for dev in data.get("devices", []):
                        self.device_combo.addItem(
                            f"CUDA:{dev['id']}  {dev['name']}", f"cuda:{dev['id']}"
                        )
                    if not data.get("devices"):
                        self.device_combo.addItem(
                            "CUDA (unavailable — CPU-only torch or driver issue)", "cpu"
                        )
                else:
                    self.device_combo.addItem(
                        f"CUDA (torch error: {data.get('error', '?')[:40]})", "cpu"
                    )
        except Exception as e:
            self.device_combo.addItem(f"CUDA (probe failed: {str(e)[:40]})", "cpu")

    def selected_device(self) -> str:
        """Return the currently selected device string e.g. 'cpu' or 'cuda:0'."""
        return self.device_combo.currentData() or "cpu"

    def _reload_model(self):
        """Restart the OCR worker process for the selected device."""
        dev = self.selected_device()
        if dev in OCRProcessManager._instances:
            OCRProcessManager._instances[dev]._stop()
            del OCRProcessManager._instances[dev]
        self.status.setText(f"OCR process restarted for {dev}")

    def _selected_or_all(self) -> str:
        text = self.text_box.textCursor().selectedText().strip()
        return text or self.text_box.toPlainText().strip()

    def set_text(self, text: str):
        current = self.text_box.toPlainText()
        self.text_box.setPlainText((current + "\n" + text) if current else text)
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
        webbrowser.open("https://jisho.org/search/" + url_quote(text))
        self.status.setText("Opened in browser ↗")

    def _search_takoboto(self):
        text = self._selected_or_all()
        if not text:
            self.status.setText("Nothing to search.")
            return
        webbrowser.open("https://takoboto.jp/?q=" + url_quote(text))
        self.status.setText("Opened in browser ↗")

    def _show_context_menu(self, pos):
        menu     = self.text_box.createStandardContextMenu()
        has_text = bool(self.text_box.toPlainText().strip())
        menu.addSeparator()
        for label, slot in [
            ("🔍  Search Jisho",    self._search_jisho),
            ("🐙  Search Takoboto", self._search_takoboto),
        ]:
            act = QAction(label, self)
            act.triggered.connect(slot)
            act.setEnabled(has_text)
            menu.addAction(act)
        menu.exec(self.text_box.viewport().mapToGlobal(pos))


# ─── Thumbnail Strip ──────────────────────────────────────────────────────────

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
            self.addItem(QListWidgetItem(QIcon(thumb), f"  {i+1}"))

    def select_page(self, index: int):
        self.setCurrentRow(index)


# ─── File Loaders ─────────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}

def load_pages_from_path(path: str) -> list[QPixmap]:
    p   = Path(path)
    ext = p.suffix.lower()
    if ext in (".cbz", ".zip"): return _load_cbz(path)
    elif ext == ".pdf":         return _load_pdf(path)
    elif ext in IMAGE_EXTS:
        px = QPixmap(path)
        return [px] if not px.isNull() else []
    elif p.is_dir():            return _load_dir(path)
    raise ValueError(f"Unsupported format: {ext}")

def _load_cbz(path: str) -> list[QPixmap]:
    pages = []
    with zipfile.ZipFile(path, "r") as zf:
        names = sorted(n for n in zf.namelist()
                       if Path(n).suffix.lower() in IMAGE_EXTS
                       and not n.startswith("__"))
        for name in names:
            img = QImage()
            img.loadFromData(zf.read(name))
            if not img.isNull():
                pages.append(QPixmap.fromImage(img))
    return pages

def _load_pdf(path: str) -> list[QPixmap]:
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF not installed.\nRun: pip install pymupdf")
    pages = []
    doc   = fitz.open(path)
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        pages.append(QPixmap.fromImage(img.copy()))
    doc.close()
    return pages

def _load_dir(path: str) -> list[QPixmap]:
    pages = []
    for f in sorted(Path(path).iterdir()):
        if f.suffix.lower() in IMAGE_EXTS:
            px = QPixmap(str(f))
            if not px.isNull():
                pages.append(px)
    return pages


# ─── Main Window ─────────────────────────────────────────────────────────────

class TakoReader(QMainWindow):
    def __init__(self):
        super().__init__()

        # Frameless — we draw our own title bar and handle resize
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window
        )

        self.setWindowTitle("Tako Reader — タコReader")
        self.resize(1280, 900)
        self.setMinimumSize(640, 480)

        self._pages: list[QPixmap]         = []
        self._current                      = 0
        self._ocr_worker: OCRWorker | None = None
        self._settings                     = QSettings("TakoReader", "TakoReaderJP")
        self._reading_mode                 = "rtl"

        # Manual resize state (used on non-Windows)
        self._resizing       = False
        self._resize_dir     = QPoint()
        self._resize_origin: QPoint | None = None
        self._resize_geo:    QRect  | None = None
        self._EDGE           = 6

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._apply_dark_theme()
        self._restore_settings()
        self.setAcceptDrops(True)

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QWidget()
        outer.setObjectName("OuterContainer")
        self.setCentralWidget(outer)

        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # Title bar
        self.title_bar = TitleBar(self)
        outer_lay.addWidget(self.title_bar)

        # Menu bar
        self.main_menu = QMenuBar()
        self.main_menu.setObjectName("MainMenuBar")
        outer_lay.addWidget(self.main_menu)

        # Toolbar widget is inserted here by _build_toolbar (called from __init__)
        self._outer_lay = outer_lay

        # Content area
        content     = QWidget()
        content_lay = QHBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self.thumb_list = ThumbnailList()
        self.thumb_list.page_selected.connect(self.go_to_page)
        content_lay.addWidget(self.thumb_list)

        center      = QWidget()
        center_lay  = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: #1a1a1a; }")

        self.page_view = PageView()
        self.page_view.ocr_requested.connect(self._run_ocr)
        self.scroll.setWidget(self.page_view)
        center_lay.addWidget(self.scroll, stretch=1)

        nav = self._build_nav_bar()
        center_lay.addWidget(nav)
        content_lay.addWidget(center, stretch=1)

        self.ocr_panel = OCRPanel()
        content_lay.addWidget(self.ocr_panel)

        outer_lay.addWidget(content, stretch=1)
        self.statusBar().showMessage("Open a file to begin (File → Open)  🐙")

    def _build_nav_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background: #141414; border-top: 1px solid #2a2a2a;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)

        self.btn_first = QPushButton("⏮")
        self.btn_prev  = QPushButton("◀  Prev")
        self.btn_next  = QPushButton("Next  ▶")
        self.btn_last  = QPushButton("⏭")

        for b in (self.btn_first, self.btn_prev, self.btn_next, self.btn_last):
            b.setFixedHeight(32)
            b.setStyleSheet("""
                QPushButton {
                    background: #2a2a2a; color: #ddd;
                    border-radius: 6px; padding: 0 14px; font-size: 10pt;
                }
                QPushButton:hover    { background: #3584e4; }
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
            QSpinBox { background: #2a2a2a; color: #ddd;
                       border: 1px solid #444; border-radius: 4px; padding: 2px 4px; }
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
        mb = self.main_menu

        file_menu = mb.addMenu("File")
        open_act  = QAction("Open…",        self, shortcut="Ctrl+O")
        open_act.triggered.connect(self.open_file)
        open_dir  = QAction("Open Folder…", self)
        open_dir.triggered.connect(self.open_folder)
        quit_act  = QAction("Quit",         self, shortcut="Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addActions([open_act, open_dir])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        view_menu = mb.addMenu("View")
        fit_w     = QAction("Fit Width",  self, shortcut="W")
        fit_w.triggered.connect(lambda: self.page_view.set_fit_mode("fit_width"))
        fit_p     = QAction("Fit Page",   self, shortcut="F")
        fit_p.triggered.connect(lambda: self.page_view.set_fit_mode("fit_page"))
        zoom_in   = QAction("Zoom In",   self, shortcut="Ctrl+=")
        zoom_in.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale * 1.2))
        zoom_out  = QAction("Zoom Out",  self, shortcut="Ctrl+-")
        zoom_out.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale / 1.2))

        self.act_thumbnails = QAction("Show Thumbnails", self, checkable=True, checked=True)
        self.act_thumbnails.triggered.connect(lambda v: self.thumb_list.setVisible(v))
        self.act_ocr_panel  = QAction("Show OCR Panel",  self, checkable=True, checked=True)
        self.act_ocr_panel.triggered.connect(lambda v: self.ocr_panel.setVisible(v))

        rtl_act = QAction("RTL (Manga)", self, checkable=True, checked=True)
        rtl_act.triggered.connect(lambda v: self._set_reading_mode("rtl" if v else "ltr"))

        view_menu.addActions([fit_w, fit_p, zoom_in, zoom_out])
        view_menu.addSeparator()
        view_menu.addActions([self.act_thumbnails, self.act_ocr_panel])
        view_menu.addSeparator()
        view_menu.addAction(rtl_act)

        nav_menu = mb.addMenu("Navigate")
        prev_a   = QAction("Previous Page", self, shortcut="Left")
        prev_a.triggered.connect(self.prev_page)
        next_a   = QAction("Next Page",     self, shortcut="Right")
        next_a.triggered.connect(self.next_page)
        nav_menu.addActions([prev_a, next_a])

        ocr_menu = mb.addMenu("OCR")
        self.act_ocr_mode = QAction("OCR Selection Mode", self,
                                    shortcut="Ctrl+Shift+O", checkable=True)
        self.act_ocr_mode.triggered.connect(self._toggle_ocr_mode)
        ocr_menu.addAction(self.act_ocr_mode)
        check_ocr = QAction("Check OCR Installation…", self)
        check_ocr.triggered.connect(self._check_ocr)
        ocr_menu.addAction(check_ocr)

    def _build_toolbar(self) -> QWidget:
        """Returns a plain QWidget toolbar that slots into the outer VBox layout."""
        from PyQt6.QtWidgets import QFrame
        bar = QWidget()
        bar.setObjectName("ToolBar")
        bar.setFixedHeight(36)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(2)

        btn_style = """
            QPushButton {
                background: transparent; color: #ccc;
                border: none; border-radius: 4px;
                padding: 4px 10px; font-size: 10pt;
            }
            QPushButton:hover   { background: #2e2e2e; color: #fff; }
            QPushButton:checked { background: #3584e4; color: #fff; }
        """

        def _btn(label, slot, checkable=False):
            b = QPushButton(label)
            b.setCheckable(checkable)
            b.setStyleSheet(btn_style)
            b.clicked.connect(slot)
            return b

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet("color: #333;")
            f.setFixedWidth(10)
            return f

        lay.addWidget(_btn("📂 Open",     self.open_file))
        lay.addWidget(_sep())
        lay.addWidget(_btn("↔ Fit Width", lambda: self.page_view.set_fit_mode("fit_width")))
        lay.addWidget(_btn("⬜ Fit Page", lambda: self.page_view.set_fit_mode("fit_page")))
        lay.addWidget(_sep())
        lay.addWidget(_btn("🔍+", lambda: self.page_view.set_scale(self.page_view._scale * 1.2)))
        lay.addWidget(_btn("🔍−", lambda: self.page_view.set_scale(self.page_view._scale / 1.2)))
        lay.addWidget(_sep())

        self.ocr_btn = _btn("🔤 OCR Mode", self._toggle_ocr_mode, checkable=True)
        lay.addWidget(self.ocr_btn)
        lay.addStretch()

        # Insert after menu bar (index 2: title_bar=0, menu=1, toolbar=2)
        self._outer_lay.insertWidget(2, bar)

    # ─────────────────────────────────────────────────────────────────────────
    # Frameless resize
    # ─────────────────────────────────────────────────────────────────────────

    # nativeEvent intentionally omitted — ctypes MSG pointer handling changed
    # in Python 3.14 and causes a hard crash on show(). Resize is handled
    # entirely via Qt mouse events on all platforms (see mousePressEvent etc.)

    # ── Mac / Linux: manual edge-resize via Qt mouse events ──────────────────

    def _hit_edges(self, pos: QPoint) -> QPoint:
        """Return (dx, dy) where -1/+1 indicates which edge is hit, 0 = none."""
        E = self._EDGE
        w, h = self.width(), self.height()
        return QPoint(
            -1 if pos.x() < E else (1 if pos.x() > w - E else 0),
            -1 if pos.y() < E else (1 if pos.y() > h - E else 0),
        )

    def _edge_cursor(self, d: QPoint) -> Qt.CursorShape:
        dx, dy = d.x(), d.y()
        if   dx and dy and dx == dy: return Qt.CursorShape.SizeFDiagCursor
        elif dx and dy:              return Qt.CursorShape.SizeBDiagCursor
        elif dx:                     return Qt.CursorShape.SizeHorCursor
        elif dy:                     return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            d = self._hit_edges(event.pos())
            if d.x() or d.y():
                self._resizing      = True
                self._resize_dir    = d
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geo    = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_origin and self._resize_geo:
            delta  = event.globalPosition().toPoint() - self._resize_origin
            geo    = QRect(self._resize_geo)
            dx, dy = self._resize_dir.x(), self._resize_dir.y()
            if dx == -1:  geo.setLeft(geo.left()    + delta.x())
            elif dx == 1: geo.setRight(geo.right()  + delta.x())
            if dy == -1:  geo.setTop(geo.top()      + delta.y())
            elif dy == 1: geo.setBottom(geo.bottom()+ delta.y())
            if geo.width() >= self.minimumWidth() and geo.height() >= self.minimumHeight():
                self.setGeometry(geo)
            event.accept()
            return
        d = self._hit_edges(event.pos())
        if d.x() or d.y():
            self.setCursor(self._edge_cursor(d))
        else:
            self.unsetCursor()  # let child widgets control their own cursors
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing      = False
            self._resize_origin = None
            self._resize_geo    = None
            self.unsetCursor()  # restore child widget cursor control
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # Window state change — keep max/restore icon in sync
    # ─────────────────────────────────────────────────────────────────────────

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.update_max_icon()

    # ─────────────────────────────────────────────────────────────────────────
    # Drag & drop
    # ─────────────────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._load_path(path)
        event.acceptProposedAction()

    # ─────────────────────────────────────────────────────────────────────────
    # File loading
    # ─────────────────────────────────────────────────────────────────────────

    def open_file(self):
        last_dir = self._settings.value("last_dir", "")
        path, _  = QFileDialog.getOpenFileName(
            self, "Open Manga File", last_dir,
            "Manga Files (*.cbz *.zip *.pdf *.jpg *.jpeg *.png *.webp *.bmp);;All Files (*)"
        )
        if path:
            self._load_path(path)

    def open_folder(self):
        last_dir = self._settings.value("last_dir", "")
        path     = QFileDialog.getExistingDirectory(self, "Open Manga Folder", last_dir)
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

        self._pages   = pages
        self._current = 0
        self._settings.setValue("last_dir", str(Path(path).parent))

        title = f"Tako Reader — {Path(path).name}"
        self.setWindowTitle(title)
        self.title_bar.set_title(title)

        self.thumb_list.load_pages(pages)
        self.page_spin.setRange(1, len(pages))
        self.go_to_page(0)
        self.statusBar().showMessage(f"Loaded {len(pages)} pages — {Path(path).name}")

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

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
        self.go_to_page(self._current + (1 if self._reading_mode == "rtl" else -1))

    def next_page(self):
        self.go_to_page(self._current + (-1 if self._reading_mode == "rtl" else 1))

    def _set_reading_mode(self, mode: str):
        self._reading_mode = mode
        self.statusBar().showMessage(
            f"Reading mode: {'Right→Left (Manga)' if mode == 'rtl' else 'Left→Right'}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # OCR
    # ─────────────────────────────────────────────────────────────────────────

    def _toggle_ocr_mode(self, checked: bool | None = None):
        if checked is None:
            checked = not self.act_ocr_mode.isChecked()
        self.act_ocr_mode.setChecked(checked)
        self.ocr_btn.setChecked(checked)
        self.page_view.set_ocr_mode(checked)
        self.statusBar().showMessage(
            "OCR mode: drag to select text region on page" if checked else "OCR mode off"
        )

    def _run_ocr(self, image: QImage, rect: QRect):
        if self._ocr_worker and self._ocr_worker.isRunning():
            return
        device = self.ocr_panel.selected_device()
        self.ocr_panel.set_status(f"⏳ Running OCR on {device}…")
        self._ocr_worker = OCRWorker(image, rect, device=device)
        self._ocr_worker.result_ready.connect(self.ocr_panel.set_text)
        self._ocr_worker.error_occurred.connect(self.ocr_panel.set_status)
        self._ocr_worker.start()

    def _check_ocr(self):
        lines = []
        try:
            import manga_ocr
            lines.append("✅ manga-ocr is installed.")
        except ImportError:
            lines.append("❌ manga-ocr is NOT installed.")
            lines.append("   Run: pip install manga-ocr")

        try:
            import torch
            lines.append(f"✅ PyTorch {torch.__version__} installed.")
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    name = torch.cuda.get_device_name(i)
                    cap  = torch.cuda.get_device_capability(i)
                    lines.append(f"✅ CUDA:{i}  {name}  (sm_{cap[0]}{cap[1]})")
            else:
                lines.append("⚠️  CUDA not available — CPU only.")
                lines.append("   For RTX 50-series, install PyTorch nightly:")
                lines.append("   pip install --pre torch --index-url")
                lines.append("   https://download.pytorch.org/whl/nightly/cu128")
        except ImportError:
            lines.append("❌ PyTorch not installed.")

        QMessageBox.information(self, "OCR Status", "\n".join(lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Keyboard shortcuts
    # ─────────────────────────────────────────────────────────────────────────

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
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        else:
            super().keyPressEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # Theme
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget          { background: #1a1a1a; color: #e0e0e0; }

            #OuterContainer               { background: #1a1a1a;
                                            border: 1px solid #3a3a3a;
                                            border-radius: 8px; }

            #TitleBar                     { background: #1e1e1e;
                                            border-bottom: 1px solid #2a2a2a;
                                            border-top-left-radius: 8px;
                                            border-top-right-radius: 8px; }
            #TitleLabel                   { color: #cccccc; font-size: 10pt; }

            #MainMenuBar                  { background: #1a1a1a; color: #ddd;
                                            border-bottom: 1px solid #2a2a2a; }
            #MainMenuBar::item:selected   { background: #3584e4; }

            QMenu                         { background: #252525; color: #ddd;
                                            border: 1px solid #3a3a3a; }
            QMenu::item:selected          { background: #3584e4; }

            #ToolBar                      { background: #1e1e1e;
                                            border-bottom: 1px solid #2a2a2a; }

            QStatusBar                    { background: #1e1e1e; color: #888;
                                            font-size: 9pt;
                                            border-top: 1px solid #2a2a2a; }

            QScrollBar:vertical           { background: #1a1a1a; width: 10px; }
            QScrollBar::handle:vertical   { background: #3a3a3a; border-radius: 5px;
                                            min-height: 30px; }
            QScrollBar:horizontal         { background: #1a1a1a; height: 10px; }
            QScrollBar::handle:horizontal { background: #3a3a3a; border-radius: 5px; }
        """)

    # ─────────────────────────────────────────────────────────────────────────
    # Settings persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _restore_settings(self):
        geo = self._settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def closeEvent(self, event):
        self._settings.setValue("geometry", self.saveGeometry())
        OCRProcessManager.shutdown_all()
        super().closeEvent(event)


# ─── Entry Point ──────────────────────────────────────────────────────────────

DEBUG = "--debug" in sys.argv


def dlog(msg: str):
    """Print only when --debug flag is passed."""
    if DEBUG:
        print(f"[tako] {msg}")


def main():
    import traceback

    dlog(f"startup begin — python {sys.version}")
    dlog(f"platform: {platform.system()} {platform.release()}")

    # Strip --debug from argv so Qt doesn't see it
    argv = [a for a in sys.argv if a != "--debug"]

    try:
        dlog("setting DPI policy...")
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

        dlog("creating QApplication...")
        app = QApplication(argv)
        app.setApplicationName("TakoReader")
        app.setOrganizationName("TakoReaderJP")

        dlog("creating TakoReader window...")
        window = TakoReader()

        dlog("calling window.show()...")
        window.show()
        dlog("window shown, entering event loop")

        if len(argv) > 1:
            dlog(f"loading file: {argv[1]}")
            window._load_path(argv[1])

        sys.exit(app.exec())

    except Exception:
        print("[tako] FATAL EXCEPTION:")
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()
