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
    QTextEdit, QListWidget, QDialog, QDialogButtonBox,
    QGroupBox, QComboBox, QFrame,
    QListWidgetItem, QSizePolicy, QRubberBand, QMessageBox,
    QProgressDialog, QMenuBar, QMenu, QCheckBox, QTextBrowser,
    QLineEdit
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QPoint, QThread, pyqtSignal,
    QSettings, QTimer
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


# ─── Icon helper ─────────────────────────────────────────────────────────────

def load_icon(name: str) -> "QIcon":
    """
    Load an icon from the icons/ folder next to this script.
    Falls back to an empty QIcon if the file is missing, so the app
    always runs even without the icon set.

    Expected location:  icons/<name>.png
    Example:            icons/open.png
    """
    path = Path(__file__).parent / "icons" / f"{name}.png"
    if path.exists():
        return QIcon(str(path))
    return QIcon()


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


# ─── OCR warmup worker ───────────────────────────────────────────────────────

class OCRWarmupWorker(QThread):
    """
    Starts the OCR subprocess in the background at app launch so the model
    is already loaded by the time the user makes their first OCR request.
    """
    ready  = pyqtSignal(str)   # emits device name on success
    failed = pyqtSignal(str)   # emits error message on failure

    def __init__(self, device: str):
        super().__init__()
        self.device = device

    def run(self):
        mgr = OCRProcessManager.get(self.device)
        if not mgr.is_alive():
            mgr._start()
        if mgr._ready:
            self.ready.emit(self.device)
        else:
            self.failed.emit(mgr._error or "OCR warmup failed")


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
        # Measure the viewport, not self — self resizes to fit the zoomed image
        sa = self._scroll_area()
        if sa:
            vw = sa.viewport().width()
            vh = sa.viewport().height()
        else:
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
        scaled = self._pixmap_orig.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        # Resize so the scroll area has real range at high zoom,
        # but never shrink below the viewport size (keeps image centred at low zoom)
        sa = self._scroll_area()
        if sa:
            vw = sa.viewport().width()
            vh = sa.viewport().height()
            self.setFixedSize(max(scaled.width(), vw), max(scaled.height(), vh))
        else:
            self.setFixedSize(scaled.width(), scaled.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-render on resize so widget size stays in sync with viewport
        if self._fit_mode != "custom":
            self._apply_fit()
        else:
            self._render()

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


# ─── Segmentation helper ─────────────────────────────────────────────────────

def _segment_japanese(text: str) -> list[str]:
    """
    Tokenise Japanese text into a list of surface forms using fugashi.
    Falls back to returning the whole string as one token if unavailable.
    """
    try:
        import fugashi
        tagger = fugashi.Tagger()
        return [w.surface for w in tagger(text) if w.surface.strip()]
    except Exception:
        return [text]


# ─── Dictionary lookup ───────────────────────────────────────────────────────

def _lookup_word(word: str) -> list[dict]:
    """
    Look up a word using jamdict.
    Returns a list of entry dicts:
      {
        "word":    str,               # the surface/kanji form
        "readings": [str],            # hiragana readings
        "senses":  [str],             # English definitions
        "kanji":   [                  # per-kanji breakdown (may be empty)
          {
            "char":    str,
            "meaning": str,
            "onyomi":  [str],
            "kunyomi": [str],
          }
        ]
      }
    Returns [] if jamdict is not installed or word not found.
    """
    try:
        from jamdict import Jamdict
        jmd = Jamdict()
        result = jmd.lookup(word)
        entries = []
        for entry in result.entries:
            readings = [str(r) for r in entry.kana_forms] or [str(k) for k in entry.kanji_forms]
            senses   = []
            for sense in entry.senses:
                gloss = "; ".join(str(g) for g in sense.gloss)
                if gloss:
                    senses.append(gloss)
            # Kanji breakdown
            kanji_info = []
            for kc in result.chars:
                meanings = [str(m) for m in kc.meanings()] if hasattr(kc, "meanings") else []
                if not meanings:
                    meanings = [str(m) for m in kc.rm_groups[0].meanings] if kc.rm_groups else []
                onyomi  = []
                kunyomi = []
                for rg in kc.rm_groups:
                    for r in rg.readings:
                        if hasattr(r, "r_type"):
                            if r.r_type == "ja_on":
                                onyomi.append(str(r))
                            elif r.r_type == "ja_kun":
                                kunyomi.append(str(r))
                kanji_info.append({
                    "char":    str(kc.literal),
                    "meaning": ", ".join(meanings[:3]),
                    "onyomi":  onyomi,
                    "kunyomi": kunyomi,
                })
            entries.append({
                "word":     word,
                "readings": readings,
                "senses":   senses,
                "kanji":    kanji_info,
            })
        return entries
    except Exception:
        return []


# ─── Furigana helper ─────────────────────────────────────────────────────────

def _make_furigana_html(word: str, reading: str) -> str:
    """
    Return an HTML string with furigana (ruby) markup for the word.
    Uses pykakasi to align kanji with their readings.
    Falls back to plain "word[reading]" if pykakasi is unavailable.

    Example output:
      <ruby>食<rt>た</rt></ruby><ruby>べ<rt></rt></ruby><ruby>る<rt></rt></ruby>
    """
    if not word:
        return word
    try:
        import pykakasi
        kks  = pykakasi.kakasi()
        items = kks.convert(word)
        parts = []
        for item in items:
            orig = item.get("orig", "")
            hira = item.get("hira", "")
            # Only add ruby if the original contains kanji
            has_kanji = any("一" <= c <= "鿿" for c in orig)
            if has_kanji and hira and hira != orig:
                parts.append(f"<ruby>{orig}<rt>{hira}</rt></ruby>")
            else:
                parts.append(orig)
        return "".join(parts)
    except Exception:
        # Graceful fallback: word + reading in brackets
        if reading and reading != word:
            return f"{word}[{reading}]"
        return word


# ─── Dictionary popup ─────────────────────────────────────────────────────────

class DictPopup(QWidget):
    """
    Floating frameless popup showing dictionary info for a Japanese word.
    Dismisses on click outside.
    """

    def __init__(self, app_settings: QSettings, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.app_settings = app_settings
        self._current_sentence = ""
        self._add_workers: set = set()   # keeps refs alive until threads finish
        self._connect_worker: "AnkiConnectWorker | None" = None
        self._fields_worker:  "AnkiFieldsWorker | None"  = None
        self.setMinimumWidth(320)
        self.setMaximumWidth(400)
        self.setMaximumHeight(520)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget {
                background: #252535;
                color: #e0e0e0;
                border: 1px solid #3a3a5a;
                border-radius: 8px;
            }
            QLabel { border: none; background: transparent; }
            QPushButton {
                background: #2a2a3a; color: #ccc;
                border: 1px solid #444; border-radius: 5px;
                padding: 4px 10px; font-size: 9pt;
            }
            QPushButton:hover { background: #3584e4; color: #fff; border-color: #3584e4; }
            QScrollBar:vertical { background: #252535; width: 6px; }
            QScrollBar::handle:vertical { background: #4a4a6a; border-radius: 3px; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent; border: none;")
        self._lay = QVBoxLayout(self._content)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(10)
        self._scroll.setWidget(self._content)

    # ── Public ────────────────────────────────────────────────────────────────

    def show_word(self, word: str, global_pos: "QPoint", sentence: str = ""):
        """Look up word, populate content, and show near global_pos."""
        self._current_sentence = sentence
        self._populate(word)
        self._reposition(global_pos)
        self.show()

    # ── Build content ─────────────────────────────────────────────────────────

    def _clear(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate(self, word: str):
        self._clear()
        entries = _lookup_word(word)

        if not entries:
            self._add_label(f"No results for <b>{word}</b>", size=10)
            self._add_buttons(word)
            self._lay.addStretch()
            self._resize_to_content()
            return

        anki_btn_style = """
            QPushButton {
                background: #4a3080; color: #ccc;
                border: 1px solid #6a50a0; border-radius: 4px;
                font-size: 8pt; padding: 2px 8px;
            }
            QPushButton:hover { background: #6a50c0; color: #fff; }
        """

        for i, entry in enumerate(entries):
            if i > 0:
                div = QFrame()
                div.setFrameShape(QFrame.Shape.HLine)
                div.setStyleSheet("color: #3a3a5a; background: #3a3a5a; border: none; max-height: 1px;")
                self._lay.addWidget(div)

            reading_str = "・".join(entry["readings"][:4]) if entry["readings"] else ""

            # Word + Anki button on the same row
            header_row = QHBoxLayout()
            header_row.setSpacing(8)
            word_lbl = QLabel(entry["word"])
            word_lbl.setFont(QFont("Noto Serif JP, serif", 20, QFont.Weight.Bold))
            word_lbl.setStyleSheet("color: #ffffff; border: none; background: transparent;")
            word_lbl.setWordWrap(True)
            header_row.addWidget(word_lbl, stretch=1)

            anki_btn = QPushButton("+ Anki")
            anki_btn.setStyleSheet(anki_btn_style)
            anki_btn.setToolTip("+ Anki  |  Ctrl+click to edit before adding")
            _word    = entry["word"]
            _reading = reading_str
            _senses  = entry["senses"]
            anki_btn.clicked.connect(
                lambda checked, w=_word, r=_reading, ss=_senses, btn=anki_btn:
                    self._handle_anki_click(
                        w, r,
                        "\n\n".join(f"{n+1}. {s}" for n, s in enumerate(ss[:6])),
                        btn
                    )
            )
            header_row.addWidget(anki_btn, alignment=Qt.AlignmentFlag.AlignTop)

            header_container = QWidget()
            header_container.setStyleSheet("background: transparent; border: none;")
            header_container.setLayout(header_row)
            self._lay.addWidget(header_container)

            if reading_str:
                self._add_label(reading_str, size=12, colour="#93b4d4")

            # Definitions — plain numbered list, no per-sense buttons
            if entry["senses"]:
                self._add_label("Definitions", size=8, colour="#666", bold=True)
                for j, sense in enumerate(entry["senses"][:6]):
                    self._add_label(f"{j+1}.  {sense}", size=9, indent=True)

            # Kanji breakdown
            if entry["kanji"]:
                self._add_label("Kanji", size=8, colour="#666", bold=True)
                for kinfo in entry["kanji"]:
                    kw = QWidget()
                    kw.setStyleSheet("background: #1e1e2e; border-radius: 6px; border: none;")
                    kl = QVBoxLayout(kw)
                    kl.setContentsMargins(10, 8, 10, 8)
                    kl.setSpacing(3)

                    char_lbl = QLabel(kinfo["char"])
                    char_lbl.setFont(QFont("Noto Serif JP, serif", 18, QFont.Weight.Bold))
                    char_lbl.setStyleSheet("color: #fff; background: transparent; border: none;")
                    kl.addWidget(char_lbl)

                    if kinfo["meaning"]:
                        m = QLabel(kinfo["meaning"])
                        m.setStyleSheet("color: #aaa; font-size: 9pt; background: transparent; border: none;")
                        m.setWordWrap(True)
                        kl.addWidget(m)

                    readings_row = QHBoxLayout()
                    readings_row.setSpacing(12)
                    if kinfo["onyomi"]:
                        on = QLabel("音: " + "、".join(kinfo["onyomi"][:4]))
                        on.setStyleSheet("color: #e8a87c; font-size: 9pt; background: transparent; border: none;")
                        readings_row.addWidget(on)
                    if kinfo["kunyomi"]:
                        kun = QLabel("訓: " + "、".join(kinfo["kunyomi"][:4]))
                        kun.setStyleSheet("color: #a8d8a8; font-size: 9pt; background: transparent; border: none;")
                        readings_row.addWidget(kun)
                    readings_row.addStretch()
                    kl.addLayout(readings_row)
                    self._lay.addWidget(kw)

        self._add_buttons(entries[0]["word"])
        self._lay.addStretch()
        self._resize_to_content()

    def _add_label(self, text: str, size: int = 10, colour: str = "#ccc",
                   bold: bool = False, indent: bool = False):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {colour}; font-size: {size}pt;"
            f"{'font-weight: bold;' if bold else ''}"
            f"{'margin-left: 8px;' if indent else ''}"
            " background: transparent; border: none;"
        )
        self._lay.addWidget(lbl)

    def _add_buttons(self, word: str):
        row = QHBoxLayout()
        row.setSpacing(6)

        jisho_btn = QPushButton("🔍 Jisho")
        jisho_btn.clicked.connect(
            lambda: webbrowser.open("https://jisho.org/search/" + url_quote(word))
        )
        tako_btn = QPushButton("🐙 Takoboto")
        tako_btn.clicked.connect(
            lambda: webbrowser.open("https://takoboto.jp/?q=" + url_quote(word))
        )

        row.addWidget(jisho_btn)
        row.addWidget(tako_btn)
        row.addStretch()

        container = QWidget()
        container.setStyleSheet("background: transparent; border: none;")
        container.setLayout(row)
        self._lay.addWidget(container)

    def _handle_anki_click(self, word: str, reading: str,
                           definition: str, btn: "QPushButton"):
        """
        Normal click  → add directly.
        Ctrl/Cmd click → open edit dialog first.
        """
        modifiers = QApplication.keyboardModifiers()
        ctrl = Qt.KeyboardModifier.ControlModifier
        if modifiers & ctrl:
            self._open_anki_edit_dialog(word, reading, definition)
        else:
            self._add_to_anki(word, reading, definition)

    def _open_anki_edit_dialog(self, word: str, reading: str, definition: str):
        """Open a dialog letting the user tweak card content before adding."""
        dlg = AnkiEditDialog(
            word, reading, definition,
            self._current_sentence,
            self.app_settings,
            parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = dlg.get_values()
            self._add_to_anki(d["word"], d["reading"], d["definition"],
                              sentence_override=d["sentence"])

    def _add_to_anki(self, word: str, reading: str, definition: str,
                     sentence_override: str | None = None):
        """Build field map from settings and call AnkiConnect addNote."""
        s = self.app_settings
        url   = s.value("anki/url",   "http://localhost:8765")
        key   = s.value("anki/key",   "")
        deck  = s.value("anki/deck",  "")
        model = s.value("anki/model", "")

        if not deck or not model:
            self._show_toast("⚠ Configure Anki in Settings first.")
            return

        # Build fields dict from saved mapping
        furigana = _make_furigana_html(word, reading)
        source_map = {
            "Word":       word,
            "Reading":    reading,
            "Furigana":   furigana,
            "Definition": definition,
            "Sentence":   sentence_override if sentence_override is not None else self._current_sentence,
        }
        fields = {}
        # Iterate all keys under anki/field/
        s.beginGroup("anki/field")
        for field_name in s.childKeys():
            source = s.value(field_name, "— skip —")
            if source != "— skip —" and source in source_map:
                fields[field_name] = source_map[source]
        s.endGroup()

        if not fields:
            self._show_toast("⚠ No field mapping configured in Settings.")
            return

        self._show_toast("⏳ Adding to Anki…", duration_ms=10000)
        worker = AnkiAddWorker(url, key, deck, model, fields)
        self._add_workers.add(worker)
        worker.finished.connect(self._on_add_finished)
        worker.finished.connect(lambda *_: self._add_workers.discard(worker))
        worker.start()

    def _on_add_finished(self, success: bool, msg: str):
        for child in self.findChildren(QLabel):
            if child.parent() is self and child.text().startswith("⏳"):
                child.deleteLater()
        if success:
            self._show_toast(f'✓ Added "{msg}" to Anki')
        else:
            self._show_toast(f"✗ Anki error: {msg}")

    def _show_toast(self, message: str, duration_ms: int = 2500):
        """Show a brief floating notification near the bottom of the popup."""
        toast = QLabel(message, self)
        toast.setStyleSheet("""
            QLabel {
                background: #2a2a3a; color: #eee;
                border: 1px solid #5a5a8a; border-radius: 6px;
                padding: 6px 12px; font-size: 9pt;
            }
        """)
        toast.adjustSize()
        # Centre horizontally, near the bottom
        x = (self.width() - toast.width()) // 2
        y = self.height() - toast.height() - 10
        toast.move(x, y)
        toast.show()
        toast.raise_()
        QTimer.singleShot(duration_ms, toast.deleteLater)

    def _resize_to_content(self):
        self._content.adjustSize()
        h = min(self._content.sizeHint().height() + 4, self.maximumHeight())
        self.resize(self.width(), h)

    def _reposition(self, global_pos: "QPoint"):
        screen = QGuiApplication.screenAt(global_pos)
        if screen:
            sg = screen.availableGeometry()
        else:
            sg = QGuiApplication.primaryScreen().availableGeometry()

        x = global_pos.x() + 12
        y = global_pos.y() + 12

        # Flip left if too close to right edge
        if x + self.width() > sg.right():
            x = global_pos.x() - self.width() - 12
        # Flip up if too close to bottom
        if y + self.height() > sg.bottom():
            y = global_pos.y() - self.height() - 12

        self.move(x, y)


# ─── OCR Sidebar ─────────────────────────────────────────────────────────────

class HoverTextBrowser(QTextBrowser):
    """QTextBrowser that tracks which anchor the cursor is currently over."""

    hovered_anchor_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_anchor = ""
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        anchor = self.anchorAt(event.pos())
        if anchor != self._current_anchor:
            self._current_anchor = anchor
            self.hovered_anchor_changed.emit(anchor)
            self.viewport().setCursor(
                Qt.CursorShape.PointingHandCursor if anchor
                else Qt.CursorShape.IBeamCursor
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._current_anchor:
            self._current_anchor = ""
            self.hovered_anchor_changed.emit("")
        super().leaveEvent(event)


# Colours used in the text browser
_TEXT_COLOUR   = "#cdd6f4"
_WORD_COLOUR   = "#93b4d4"
_WORD_HOVER    = "#1e1e2e"
_WORD_HOVER_BG = "#93b4d4"
_BG_COLOUR     = "#1e1e2e"

_CARD_STYLE = """
    QWidget#OCRCard {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 6px;
    }
"""

_BTN_SUBTLE = """
    QPushButton {
        background: transparent; color: #555;
        border: none; font-size: 9pt; padding: 2px 4px;
    }
    QPushButton:hover { color: #ccc; background: #2a2a3a; border-radius: 3px; }
"""


class OCRCard(QWidget):
    """
    A single OCR result card. Each rubber-band selection produces one card.
    Newest cards are inserted at the top of the panel's scroll area.
    """
    word_clicked     = pyqtSignal(str, str)   # word, own raw_text (as sentence)
    merge_requested  = pyqtSignal(object)     # emits self
    dismiss_requested = pyqtSignal(object)    # emits self

    def __init__(self, raw_text: str, segmentation_on: bool,
                 dict_popup, parent=None):
        super().__init__(parent)
        self.setObjectName("OCRCard")
        self.setStyleSheet(_CARD_STYLE)
        self._raw_text       = raw_text
        self._segmentation_on = segmentation_on
        self._hovered_word   = ""
        self._last_hovered   = ""
        self._dict_popup     = dict_popup

        # Use a plain layout — buttons float over the browser as an overlay
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Text browser fills the card ──
        self.browser = HoverTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setFont(QFont("Noto Serif JP, serif", 16))
        self.browser.setStyleSheet(f"""
            QTextBrowser {{
                background: {_BG_COLOUR};
                color: {_TEXT_COLOUR};
                border: none;
                border-radius: 6px;
                padding: 6px 6px 24px 6px;
                font-size: 18px;
            }}
        """)
        self.browser.anchorClicked.connect(self._on_word_clicked)
        self.browser.hovered_anchor_changed.connect(self._on_hover_changed)
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self._show_context_menu)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.browser)

        # ── Button row overlaid at bottom-right of the browser ──
        self._btn_bar = QWidget(self)
        self._btn_bar.setStyleSheet("background: transparent;")
        btn_lay = QHBoxLayout(self._btn_bar)
        btn_lay.setContentsMargins(0, 0, 4, 2)
        btn_lay.setSpacing(2)
        btn_lay.addStretch()

        self._merge_btn = QPushButton()
        self._merge_btn.setToolTip("Merge with card above")
        self._merge_btn.setStyleSheet(_BTN_SUBTLE)
        self._merge_btn.setFixedSize(22, 18)
        ic_merge = load_icon("merge")
        if not ic_merge.isNull():
            self._merge_btn.setIcon(ic_merge)
            self._merge_btn.setIconSize(QSize(12, 12))
        else:
            self._merge_btn.setText("↕")
        self._merge_btn.clicked.connect(lambda: self.merge_requested.emit(self))
        btn_lay.addWidget(self._merge_btn)

        copy_btn = QPushButton()
        copy_btn.setToolTip("Copy text")
        copy_btn.setStyleSheet(_BTN_SUBTLE)
        copy_btn.setFixedSize(22, 18)
        ic_copy = load_icon("copy")
        if not ic_copy.isNull():
            copy_btn.setIcon(ic_copy)
            copy_btn.setIconSize(QSize(12, 12))
        else:
            copy_btn.setText("C")
        copy_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self._raw_text)
        )
        btn_lay.addWidget(copy_btn)

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setToolTip("Dismiss")
        dismiss_btn.setStyleSheet(_BTN_SUBTLE)
        dismiss_btn.setFixedSize(22, 18)
        dismiss_btn.clicked.connect(lambda: self.dismiss_requested.emit(self))
        btn_lay.addWidget(dismiss_btn)

        # documentSizeChanged fires after layout is complete — reliable for initial size
        self.browser.document().documentLayout().documentSizeChanged.connect(
            lambda _: self._fit_browser_height()
        )
        self._render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def set_segmentation(self, on: bool):
        self._segmentation_on = on
        self._hovered_word = ""
        self._render()

    def _render(self):
        raw = self._raw_text
        if self._segmentation_on:
            words = _segment_japanese(raw)
            parts = []
            for word in words:
                esc = (word.replace("&","&amp;").replace("<","&lt;")
                           .replace(">","&gt;").replace('"',"&quot;"))
                if esc == self._hovered_word:
                    parts.append(
                        f'<a href="{esc}" style="color:{_WORD_HOVER};'
                        f'background-color:{_WORD_HOVER_BG};'
                        f'border-radius:3px;padding:0 2px;'
                        f'text-decoration:none;">{esc}</a>'
                    )
                else:
                    parts.append(
                        f'<a href="{esc}" style="color:{_WORD_COLOUR};'
                        f'text-decoration:none;">{esc}</a>'
                    )
            body = "".join(parts)
        else:
            esc = (raw.replace("&","&amp;").replace("<","&lt;")
                      .replace(">","&gt;"))
            body = f'<span style="color:{_TEXT_COLOUR};">{esc}</span>'

        font_style = "font-family:'Noto Serif JP',serif;font-size:18px;"
        html = f'<div style="{font_style}">{body}</div>'
        self.browser.setHtml(html)

    def _fit_browser_height(self):
        """Resize browser to content and position the button overlay at the bottom."""
        doc_h = int(self.browser.document().size().height())
        # Extra 24px bottom padding makes room for the button bar overlay
        h = max(doc_h + 28, 48)
        self.browser.setFixedHeight(h)
        self.setFixedHeight(h)
        # Position btn_bar at bottom-right of the card
        bw = self._btn_bar.sizeHint().width()
        self._btn_bar.setGeometry(0, h - 22, self.width(), 22)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_hover_changed(self, anchor: str):
        if not self._segmentation_on:
            return
        self._hovered_word = anchor
        self._last_hovered = anchor
        self._render()

    def _on_word_clicked(self, url):
        word = url.toString()
        if word:
            self.word_clicked.emit(word, self._raw_text)

    def _show_context_menu(self, pos):
        menu = self.browser.createStandardContextMenu()
        menu.addSeparator()
        if self._segmentation_on:
            lookup_word = self._last_hovered
        else:
            lookup_word = self.browser.textCursor().selectedText().strip()
        dict_act = QAction("📚  Look Up in Dictionary", self)
        dict_act.triggered.connect(
            lambda: self._do_lookup(lookup_word)
        )
        dict_act.setEnabled(bool(lookup_word))
        menu.addAction(dict_act)
        menu.addSeparator()
        for label, url_tpl in [
            ("🔍  Search Jisho",    "https://jisho.org/search/{}"),
            ("🐙  Search Takoboto", "https://takoboto.jp/?q={}"),
        ]:
            text = lookup_word or self._raw_text
            act  = QAction(label, self)
            act.triggered.connect(
                lambda _, u=url_tpl, t=text: webbrowser.open(u.format(url_quote(t)))
            )
            act.setEnabled(bool(text))
            menu.addAction(act)
        menu.exec(self.browser.viewport().mapToGlobal(pos))

    def _do_lookup(self, word: str):
        if word and self._dict_popup:
            self._dict_popup.show_word(word, QCursor.pos(),
                                       sentence=self._raw_text)

    # ── Merge ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self.height()
        if h > 0:
            self._btn_bar.setGeometry(0, h - 22, self.width(), 22)

    def absorb(self, other: "OCRCard"):
        """Append other's text to this card (merge down into self)."""
        self._raw_text = self._raw_text + " " + other._raw_text
        self._render()

    def set_merge_visible(self, visible: bool):
        self._merge_btn.setVisible(visible)

    @property
    def raw_text(self) -> str:
        return self._raw_text


class OCRPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(280)
        self._segmentation_on = False
        self._dict_popup      = None
        self._app_settings    = None
        self._cards: list[OCRCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Header ──
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self.ocr_indicator = QLabel("⬤")
        self.ocr_indicator.setToolTip("OCR status: idle")
        self.ocr_indicator.setStyleSheet("color: #444; font-size: 8pt;")
        self.ocr_indicator.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self.ocr_indicator)

        title = QLabel("OCR / Text")
        title.setStyleSheet("color: #888; font-size: 9pt;")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(title, stretch=1)

        self.seg_check = QPushButton("Segment")
        self.seg_check.setCheckable(True)
        self.seg_check.setChecked(False)
        self.seg_check.setToolTip("Tokenise text into words.\nClick a word to look it up.")
        self.seg_check.setStyleSheet("""
            QPushButton {
                background: transparent; color: #666;
                border: 1px solid #444; border-radius: 4px;
                padding: 2px 8px; font-size: 9pt;
            }
            QPushButton:hover   { color: #aaa; border-color: #666; }
            QPushButton:checked { background: #3584e4; color: #fff; border-color: #3584e4; }
        """)
        self.seg_check.clicked.connect(self._on_seg_toggled)
        header_row.addWidget(self.seg_check)
        layout.addLayout(header_row)

        # ── Scroll area containing cards ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._card_container = QWidget()
        self._card_container.setStyleSheet("background: transparent;")
        self._card_lay = QVBoxLayout(self._card_container)
        self._card_lay.setContentsMargins(0, 0, 0, 0)
        self._card_lay.setSpacing(6)
        self._card_lay.addStretch()   # pushes cards toward the top

        self._scroll.setWidget(self._card_container)
        layout.addWidget(self._scroll, stretch=1)

        # ── Bottom bar ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self.clear_all)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    # ── Settings wiring ───────────────────────────────────────────────────────

    def set_settings(self, app_settings: QSettings):
        self._app_settings = app_settings
        self._dict_popup   = DictPopup(app_settings)
        # Back-fill popup ref into any cards already created (shouldn't happen
        # in practice but guards against ordering edge cases)
        for card in self._cards:
            card._dict_popup = self._dict_popup

    # ── Segmentation ─────────────────────────────────────────────────────────

    def _on_seg_toggled(self):
        self._segmentation_on = self.seg_check.isChecked()
        for card in self._cards:
            card.set_segmentation(self._segmentation_on)

    # ── Card management ───────────────────────────────────────────────────────

    def _add_card(self, raw_text: str):
        card = OCRCard(raw_text, self._segmentation_on,
                       self._dict_popup, parent=self._card_container)
        card.word_clicked.connect(self._on_card_word_clicked)
        card.merge_requested.connect(self._on_merge_requested)
        card.dismiss_requested.connect(self._on_dismiss_requested)
        # Insert at top (index 0), above the stretch
        self._card_lay.insertWidget(0, card)
        self._cards.insert(0, card)
        self._update_merge_buttons()
        # Scroll to top so newest card is visible
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _update_merge_buttons(self):
        """Only show merge button on cards that have a card above them."""
        for i, card in enumerate(self._cards):
            # _cards[0] is newest (top); merge means append to card above = _cards[i-1]
            card.set_merge_visible(i > 0)

    def _on_card_word_clicked(self, word: str, sentence: str):
        if self._dict_popup:
            self._dict_popup.show_word(word, QCursor.pos(), sentence=sentence)
        self.status.setText(f"Looking up: {word}")

    def _on_merge_requested(self, card: OCRCard):
        idx = self._cards.index(card)
        if idx == 0:
            return  # no card above
        above = self._cards[idx - 1]
        above.absorb(card)
        self._remove_card(card)

    def _on_dismiss_requested(self, card: OCRCard):
        self._remove_card(card)

    def _remove_card(self, card: OCRCard):
        if card in self._cards:
            self._cards.remove(card)
        self._card_lay.removeWidget(card)
        card.deleteLater()
        self._update_merge_buttons()

    def clear_all(self):
        for card in list(self._cards):
            self._card_lay.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self.status.setText("")

    # ── Public API (called by TakoReader) ─────────────────────────────────────

    def set_text(self, text: str):
        self._add_card(text)
        self.status.setText("✓ OCR complete")

    def set_ocr_state(self, state: str):
        """
        Update the OCR indicator in the panel header.
        state: "idle" | "loading" | "ready" | "error"
        """
        styles = {
            "idle":    ("⬤", "#444",    "OCR status: idle"),
            "loading": ("⬤", "#e6a817", "OCR model loading…"),
            "ready":   ("⬤", "#2ecc71", "OCR model ready"),
            "error":   ("⬤", "#e74c3c", "OCR failed to load"),
        }
        dot, colour, tip = styles.get(state, styles["idle"])
        self.ocr_indicator.setText(dot)
        self.ocr_indicator.setStyleSheet(f"color: {colour}; font-size: 8pt;")
        self.ocr_indicator.setToolTip(tip)

    def set_status(self, msg: str):
        self.status.setText(msg)

    def lookup_shortcut(self):
        """Ctrl+D: look up last hovered word across all cards."""
        if self._segmentation_on:
            # Find the most recently hovered word across all cards
            for card in self._cards:
                if card._last_hovered:
                    self._on_card_word_clicked(card._last_hovered, card.raw_text)
                    return
        else:
            # Look for selected text in any card's browser
            for card in self._cards:
                sel = card.browser.textCursor().selectedText().strip()
                if sel:
                    self._on_card_word_clicked(sel, card.raw_text)
                    return


# ─── AnkiConnect helper ──────────────────────────────────────────────────────# ─── AnkiConnect helper ──────────────────────────────────────────────────────

def _anki_request(action: str, url: str, api_key: str = "", **params) -> dict:
    """
    Send a single AnkiConnect request.
    Returns the parsed response dict, or raises on network/API error.
    """
    import urllib.request, json as _json
    payload = {"action": action, "version": 6, "params": params}
    if api_key:
        payload["key"] = api_key
    data = _json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = _json.loads(resp.read())
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result.get("result")


def anki_test_connection(url: str, api_key: str = "") -> bool:
    """Return True if AnkiConnect responds."""
    try:
        _anki_request("version", url, api_key)
        return True
    except Exception:
        return False


def anki_get_decks(url: str, api_key: str = "") -> list[str]:
    try:
        return sorted(_anki_request("deckNames", url, api_key))
    except Exception:
        return []


def anki_get_note_types(url: str, api_key: str = "") -> list[str]:
    try:
        return sorted(_anki_request("modelNames", url, api_key))
    except Exception:
        return []


def anki_get_fields(url: str, api_key: str, model_name: str) -> list[str]:
    try:
        return _anki_request("modelFieldNames", url, api_key,
                              modelName=model_name)
    except Exception:
        return []


def anki_add_note(url: str, api_key: str, deck: str, model: str,
                  fields: dict, tags: list[str] | None = None) -> int | None:
    """
    Add a note to Anki. fields = {"Field Name": "value", ...}
    Returns the new note ID, or None on failure.
    """
    try:
        note = {
            "deckName":  deck,
            "modelName": model,
            "fields":    fields,
            "options":   {"allowDuplicate": False},
            "tags":      tags or ["tako-reader"],
        }
        return _anki_request("addNote", url, api_key, note=note)
    except Exception as e:
        raise RuntimeError(str(e))


# ─── AnkiConnect background workers ──────────────────────────────────────────

class AnkiConnectWorker(QThread):
    """Fetch decks + note types in one shot after a connection test."""
    finished = pyqtSignal(bool, list, list, str)  # ok, decks, models, error

    def __init__(self, url: str, api_key: str):
        super().__init__()
        self.url     = url
        self.api_key = api_key

    def run(self):
        try:
            if not anki_test_connection(self.url, self.api_key):
                self.finished.emit(False, [], [], "Could not connect — is Anki running?")
                return
            decks  = anki_get_decks(self.url, self.api_key)
            models = anki_get_note_types(self.url, self.api_key)
            self.finished.emit(True, decks, models, "")
        except Exception as e:
            self.finished.emit(False, [], [], str(e))


class AnkiFieldsWorker(QThread):
    """Fetch field names for a note type."""
    finished = pyqtSignal(list)

    def __init__(self, url: str, api_key: str, model: str):
        super().__init__()
        self.url     = url
        self.api_key = api_key
        self.model   = model

    def run(self):
        try:
            self.finished.emit(anki_get_fields(self.url, self.api_key, self.model))
        except Exception:
            self.finished.emit([])


class AnkiAddWorker(QThread):
    """Add a note to Anki without blocking the UI."""
    finished = pyqtSignal(bool, str)  # success, word/error message

    def __init__(self, url: str, api_key: str, deck: str, model: str, fields: dict):
        super().__init__()
        self.url     = url
        self.api_key = api_key
        self.deck    = deck
        self.model   = model
        self.fields  = fields
        self._word   = next(iter(fields.values()), "") if fields else ""

    def run(self):
        try:
            anki_add_note(self.url, self.api_key, self.deck, self.model, self.fields)
            self.finished.emit(True, self._word)
        except Exception as e:
            self.finished.emit(False, str(e)[:80])


# ─── Anki edit dialog ────────────────────────────────────────────────────────

class AnkiEditDialog(QDialog):
    """
    Shown when the user Ctrl/Cmd-clicks "+ Anki".
    Lets them edit word, reading, furigana, definition, and sentence
    before the card is added.
    """

    def __init__(self, word: str, reading: str, definition: str,
                 sentence: str, app_settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Card — Tako Reader")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog  { background: #1a1a1a; color: #e0e0e0; }
            QLabel   { color: #aaa; font-size: 9pt; }
            QTextEdit, QLineEdit {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 6px; font-size: 10pt;
            }
            QPushButton {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 5px;
                padding: 5px 18px; font-size: 10pt;
            }
            QPushButton:hover { background: #3584e4; color: #fff; border-color: #3584e4; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        furigana_html = _make_furigana_html(word, reading)

        fields = [
            ("Word",       word,          False),
            ("Reading",    reading,       False),
            ("Furigana",   furigana_html, False),
            ("Definition", definition,    True),
            ("Sentence",   sentence,      True),
        ]

        self._editors: dict[str, QWidget] = {}
        field_style = """
            QLineEdit, QTextEdit {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 6px; font-size: 10pt;
            }
        """
        for label, value, multiline in fields:
            lbl = QLabel(label)
            root.addWidget(lbl)
            if multiline:
                w = QTextEdit()
                w.setPlainText(value)
                w.setFixedHeight(72)
                w.setStyleSheet(field_style)
            else:
                w = QLineEdit(value)
                w.setStyleSheet(field_style)
            root.addWidget(w)
            self._editors[label] = w

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def get_values(self) -> dict[str, str]:
        result = {}
        for key, widget in self._editors.items():
            if isinstance(widget, QTextEdit):
                result[key.lower()] = widget.toPlainText()
            else:
                result[key.lower()] = widget.text()
        return result


# ─── Settings Dialog ─────────────────────────────────────────────────────────

def _probe_cuda_devices() -> list[dict]:
    """Probe for CUDA devices via subprocess so DLL crashes cannot affect the app."""
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
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=15
        )
        if result.stdout.strip():
            data = _json.loads(result.stdout.strip().splitlines()[-1])
            if data.get("ok"):
                return data.get("devices", [])
    except Exception:
        pass
    return []


class SettingsDialog(QDialog):
    """
    Modal settings window. Changes are only applied when Save is clicked.
    Reads and writes values via QSettings so they persist across sessions.
    """

    def __init__(self, app_settings: QSettings, parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self.setWindowTitle("Tako Reader — Settings")
        self.setMinimumWidth(480)
        self.setMinimumHeight(300)
        self.setModal(True)
        self._apply_style()
        self._build_ui()
        self._load_values()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 16)
        root.setSpacing(0)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        content = QWidget()
        self._content_lay = QVBoxLayout(content)
        self._content_lay.setContentsMargins(24, 20, 24, 8)
        self._content_lay.setSpacing(20)

        self._build_general_section()
        self._build_ocr_section()
        self._build_anki_section()

        self._content_lay.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #2a2a2a;")
        root.addWidget(div)

        # Save / Cancel buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.setContentsMargins(24, 8, 24, 0)
        btn_box.accepted.connect(self._save)
        btn_box.rejected.connect(self.reject)
        btn_box.setStyleSheet("""
            QPushButton {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 6px;
                padding: 6px 20px; font-size: 10pt; min-width: 80px;
            }
            QPushButton:hover { background: #3584e4; color: #fff; border-color: #3584e4; }
        """)
        root.addWidget(btn_box)

    def _section(self, title: str) -> QVBoxLayout:
        """Create a titled group box and return its inner layout."""
        box = QGroupBox(title)
        box.setStyleSheet("""
            QGroupBox {
                color: #aaa; font-size: 9pt; font-weight: bold;
                border: 1px solid #2a2a2a; border-radius: 6px;
                margin-top: 8px; padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; subcontrol-position: top left;
                left: 10px; padding: 0 4px;
            }
        """)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        self._content_lay.addWidget(box)
        return lay

    def _row(self, layout: QVBoxLayout, label: str, widget: QWidget, hint: str = ""):
        """Add a labelled row to a section layout."""
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(140)
        lbl.setStyleSheet("color: #ccc; font-size: 10pt;")
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        layout.addLayout(row)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setStyleSheet("color: #666; font-size: 8pt;")
            hint_lbl.setWordWrap(True)
            layout.addWidget(hint_lbl)

    # ── General section ──────────────────────────────────────────────────────

    def _build_general_section(self):
        lay = self._section("General")

        self.session_memory_check = QCheckBox()
        self.session_memory_check.setStyleSheet("""
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #444; border-radius: 3px;
                background: #2a2a2a;
            }
            QCheckBox::indicator:checked {
                background: #3584e4; border-color: #3584e4;
            }
        """)
        self._row(lay, "Session Memory", self.session_memory_check,
                  hint="Remember the last opened file and page position. "
                       "Reopening Tako Reader will continue where you left off.")

    # ── OCR section ───────────────────────────────────────────────────────────

    def _build_ocr_section(self):
        lay = self._section("OCR")

        self.ocr_device_combo = QComboBox()
        self.ocr_device_combo.setStyleSheet("""
            QComboBox {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 8px; font-size: 10pt;
            }
            QComboBox::drop-down {
                border-left: 1px solid #444;
                width: 24px;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0; height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #888;
            }
            QComboBox::drop-down:hover { background: #3a3a4a; }
            QComboBox::down-arrow:hover { border-top-color: #ddd; }
            QComboBox QAbstractItemView {
                background: #252525; color: #ddd;
                selection-background-color: #3584e4;
                border: 1px solid #3a3a3a;
            }
        """)
        self._populate_device_combo()
        self._row(lay, "OCR Device", self.ocr_device_combo,
                  hint="CPU works on all systems. CUDA requires a compatible NVIDIA GPU "
                       "and a CUDA-enabled PyTorch build. Changes take effect on the "
                       "next OCR call.")

        self.ocr_clear_on_file_check = QCheckBox()
        self.ocr_clear_on_file_check.setStyleSheet("""
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #444; border-radius: 3px; background: #2a2a2a;
            }
            QCheckBox::indicator:checked { background: #3584e4; border-color: #3584e4; }
        """)
        self._row(lay, "Clear on File Change", self.ocr_clear_on_file_check,
                  hint="Clear the OCR panel when a new file is opened.")

        self.ocr_warmup_check = QCheckBox()
        self.ocr_warmup_check.setStyleSheet("""
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #444; border-radius: 3px; background: #2a2a2a;
            }
            QCheckBox::indicator:checked { background: #3584e4; border-color: #3584e4; }
        """)
        self._row(lay, "Load at Startup", self.ocr_warmup_check,
                  hint="Pre-load the OCR model when Tako Reader starts so the "
                       "first OCR call is instant. Adds a few seconds to launch time.")

    def _populate_device_combo(self):
        self.ocr_device_combo.clear()
        self.ocr_device_combo.addItem("CPU", "cpu")
        devices = _probe_cuda_devices()
        if devices:
            for dev in devices:
                self.ocr_device_combo.addItem(
                    f"CUDA:{dev['id']}  {dev['name']}", f"cuda:{dev['id']}"
                )
        else:
            self.ocr_device_combo.addItem(
                "CUDA (unavailable — see Troubleshooting in README)", "cpu"
            )

    # ── Anki section ─────────────────────────────────────────────────────────

    def _build_anki_section(self):
        lay = self._section("Anki")

        # Connection row
        self.anki_url = QLineEdit()
        self.anki_url.setPlaceholderText("http://localhost:8765")
        self.anki_url.setStyleSheet("""
            QLineEdit {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 8px; font-size: 10pt;
            }
        """)
        self._row(lay, "AnkiConnect URL", self.anki_url)

        self.anki_key = QLineEdit()
        self.anki_key.setPlaceholderText("Leave blank if not set")
        self.anki_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.anki_key.setStyleSheet(self.anki_url.styleSheet())
        self._row(lay, "API Key", self.anki_key,
                  hint="Only needed if you have configured an API key in AnkiConnect.")

        # Connect button + status
        connect_row = QHBoxLayout()
        self._anki_status = QLabel("Not connected")
        self._anki_status.setStyleSheet("color: #666; font-size: 9pt;")
        connect_btn = QPushButton("Test Connection")
        connect_btn.setStyleSheet("""
            QPushButton {
                background: #2a2a2a; color: #ccc;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 12px; font-size: 9pt;
            }
            QPushButton:hover { background: #3584e4; color: #fff; border-color: #3584e4; }
        """)
        connect_btn.clicked.connect(self._anki_connect)
        self._connect_btn    = connect_btn
        self._connect_worker = None
        self._fields_worker  = None
        connect_row.addWidget(self._anki_status, stretch=1)
        connect_row.addWidget(connect_btn)
        lay.addLayout(connect_row)

        # Deck picker
        self.anki_deck = QComboBox()
        self.anki_deck.setEditable(True)
        self.anki_deck.setStyleSheet("""
            QComboBox {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 8px; font-size: 10pt;
            }
            QComboBox::drop-down {
                border-left: 1px solid #444; width: 24px;
                border-top-right-radius: 4px; border-bottom-right-radius: 4px;
            }
            QComboBox::down-arrow {
                image: none; width: 0; height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #888;
            }
            QComboBox::drop-down:hover { background: #3a3a4a; }
            QComboBox::down-arrow:hover { border-top-color: #ddd; }
            QComboBox QAbstractItemView {
                background: #252525; color: #ddd;
                selection-background-color: #3584e4; border: 1px solid #3a3a3a;
            }
        """)
        self._row(lay, "Deck", self.anki_deck)

        # Note type picker
        self.anki_model = QComboBox()
        self.anki_model.setEditable(True)
        self.anki_model.setStyleSheet(self.anki_deck.styleSheet())
        self.anki_model.currentTextChanged.connect(self._anki_model_changed)
        self._row(lay, "Note Type", self.anki_model)

        # Field mapping — dynamically built when model changes
        self._field_map_lay = QVBoxLayout()
        self._field_map_lay.setSpacing(6)
        self._field_widgets: dict[str, QComboBox] = {}  # field_name → combo
        lay.addLayout(self._field_map_lay)

        # Mapping hint
        self._field_hint = QLabel("Connect to Anki to configure field mapping.")
        self._field_hint.setStyleSheet("color: #666; font-size: 8pt;")
        self._field_hint.setWordWrap(True)
        lay.addWidget(self._field_hint)

    def _anki_connect(self):
        url     = self.anki_url.text().strip() or "http://localhost:8765"
        api_key = self.anki_key.text().strip()

        self._connect_btn.setEnabled(False)
        self._anki_status.setText("Connecting…")
        self._anki_status.setStyleSheet("color: #888; font-size: 9pt;")

        self._connect_worker = AnkiConnectWorker(url, api_key)
        self._connect_worker.finished.connect(self._on_connect_finished)
        self._connect_worker.start()

    def _on_connect_finished(self, ok: bool, decks: list, models: list, error: str):
        self._connect_btn.setEnabled(True)
        if not ok:
            self._anki_status.setText(f"✗ {error}")
            self._anki_status.setStyleSheet("color: #e74c3c; font-size: 9pt;")
            return

        self._anki_status.setText("✓ Connected")
        self._anki_status.setStyleSheet("color: #2ecc71; font-size: 9pt;")

        self.app_settings.setValue("anki/cached_decks",  decks)
        self.app_settings.setValue("anki/cached_models", models)

        saved_deck  = self.app_settings.value("anki/deck",  "")
        saved_model = self.app_settings.value("anki/model", "")

        self.anki_model.blockSignals(True)
        self.anki_deck.clear()
        self.anki_deck.addItems(decks)
        if saved_deck in decks:
            self.anki_deck.setCurrentText(saved_deck)
        self.anki_model.clear()
        self.anki_model.addItems(models)
        if saved_model in models:
            self.anki_model.setCurrentText(saved_model)
        self.anki_model.blockSignals(False)

        if self.anki_model.currentText():
            self._anki_model_changed(self.anki_model.currentText())

    def _anki_model_changed(self, model_name: str):
        """Fetch fields for the selected note type in a background thread."""
        if not model_name:
            return
        url     = self.anki_url.text().strip() or "http://localhost:8765"
        api_key = self.anki_key.text().strip()

        self._field_hint.setText("Loading fields…")
        while self._field_map_lay.count():
            item = self._field_map_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._field_widgets.clear()

        self._fields_worker = AnkiFieldsWorker(url, api_key, model_name)
        self._fields_worker.finished.connect(
            lambda fields: self._on_fields_fetched(fields, model_name)
        )
        self._fields_worker.start()

    def _on_fields_fetched(self, fields: list, model_name: str):
        """Populate field mapping rows once fields arrive from the worker."""
        if not fields:
            self._field_hint.setText("Connect to Anki to configure field mapping.")
            return

        self._field_hint.setText(
            "Map each Anki field to Tako Reader data. "
            "Fields set to '\u2014 skip \u2014' will be left blank."
        )

        SOURCES = ["— skip —", "Word", "Reading", "Furigana", "Definition", "Sentence"]
        combo_style = """
            QComboBox {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 3px 6px; font-size: 9pt;
            }
            QComboBox::drop-down {
                border-left: 1px solid #444; width: 22px;
                border-top-right-radius: 4px; border-bottom-right-radius: 4px;
            }
            QComboBox::down-arrow {
                image: none; width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #888;
            }
            QComboBox::drop-down:hover { background: #3a3a4a; }
            QComboBox::down-arrow:hover { border-top-color: #ddd; }
            QComboBox QAbstractItemView {
                background: #252525; color: #ddd;
                selection-background-color: #3584e4;
            }
        """

        for field in fields:
            row = QHBoxLayout()
            lbl = QLabel(field)
            lbl.setFixedWidth(130)
            lbl.setStyleSheet("color: #bbb; font-size: 9pt;")
            combo = QComboBox()
            combo.addItems(SOURCES)
            combo.setStyleSheet(combo_style)

            saved = self.app_settings.value(f"anki/field/{field}", "— skip —")
            if saved in SOURCES:
                combo.setCurrentText(saved)

            row.addWidget(lbl)
            row.addWidget(combo, stretch=1)
            container = QWidget()
            container.setStyleSheet("background: transparent; border: none;")
            container.setLayout(row)
            self._field_map_lay.addWidget(container)
            self._field_widgets[field] = combo


    def _load_values(self):
        """Populate all widgets from saved QSettings values."""
        # General
        session_on = self.app_settings.value("general/session_memory", True, type=bool)
        self.session_memory_check.setChecked(session_on)

        # OCR
        saved_device = self.app_settings.value("ocr/device", "cpu")
        for i in range(self.ocr_device_combo.count()):
            if self.ocr_device_combo.itemData(i) == saved_device:
                self.ocr_device_combo.setCurrentIndex(i)
                break
        warmup_on = self.app_settings.value("ocr/warmup", False, type=bool)
        self.ocr_warmup_check.setChecked(warmup_on)
        clear_on_file = self.app_settings.value("ocr/clear_on_file", True, type=bool)
        self.ocr_clear_on_file_check.setChecked(clear_on_file)

        # Anki — restore URL/key and pre-populate from cache for instant display
        self.anki_url.setText(
            self.app_settings.value("anki/url", "http://localhost:8765")
        )
        self.anki_key.setText(self.app_settings.value("anki/key", ""))

        cached_decks  = self.app_settings.value("anki/cached_decks",  []) or []
        cached_models = self.app_settings.value("anki/cached_models", []) or []
        saved_deck    = self.app_settings.value("anki/deck",  "")
        saved_model   = self.app_settings.value("anki/model", "")

        if cached_decks:
            self.anki_deck.blockSignals(True)
            self.anki_deck.addItems(cached_decks)
            if saved_deck in cached_decks:
                self.anki_deck.setCurrentText(saved_deck)
            self.anki_deck.blockSignals(False)

        if cached_models:
            self.anki_model.blockSignals(True)
            self.anki_model.addItems(cached_models)
            if saved_model in cached_models:
                self.anki_model.setCurrentText(saved_model)
            self.anki_model.blockSignals(False)
            if saved_model:
                url     = self.app_settings.value("anki/url", "http://localhost:8765")
                api_key = self.app_settings.value("anki/key", "")
                self._fields_worker = AnkiFieldsWorker(url, api_key, saved_model)
                self._fields_worker.finished.connect(
                    lambda fields: self._on_fields_fetched(fields, saved_model)
                )
                self._fields_worker.start()

    def _save(self):
        """Persist all values to QSettings and close."""
        self.app_settings.setValue("general/session_memory",
                                   self.session_memory_check.isChecked())
        self.app_settings.setValue("ocr/device",  self.ocr_device_combo.currentData())
        self.app_settings.setValue("ocr/warmup",       self.ocr_warmup_check.isChecked())
        self.app_settings.setValue("ocr/clear_on_file", self.ocr_clear_on_file_check.isChecked())

        # Anki
        self.app_settings.setValue("anki/url",   self.anki_url.text().strip())
        self.app_settings.setValue("anki/key",   self.anki_key.text().strip())
        self.app_settings.setValue("anki/deck",  self.anki_deck.currentText())
        self.app_settings.setValue("anki/model", self.anki_model.currentText())
        for field, combo in self._field_widgets.items():
            self.app_settings.setValue(f"anki/field/{field}", combo.currentText())

        self.accept()

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog { background: #1a1a1a; color: #e0e0e0; }
            QLabel  { color: #e0e0e0; }
        """)


# ─── Bookmark Popup ──────────────────────────────────────────────

class BookmarkPopup(QWidget):
    navigate         = pyqtSignal(int)
    rename_requested = pyqtSignal(int, str)
    remove_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)
        self.setMaximumHeight(480)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget {
                background: #252535; color: #e0e0e0;
                border: 1px solid #3a3a5a; border-radius: 8px;
            }
            QLabel   { border: none; background: transparent; }
            QLineEdit {
                background: #1e1e2e; color: #ddd;
                border: 1px solid #3a3a5a; border-radius: 4px;
                padding: 2px 6px; font-size: 9pt;
            }
            QPushButton {
                background: #2a2a3a; color: #ccc;
                border: 1px solid #444; border-radius: 4px;
                padding: 3px 10px; font-size: 9pt;
            }
            QPushButton:hover { background: #3584e4; color: #fff; border-color: #3584e4; }
            QScrollBar:vertical { background: #252535; width: 6px; }
            QScrollBar::handle:vertical { background: #4a4a6a; border-radius: 3px; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setStyleSheet(
            "background: #1e1e2e; border-bottom: 1px solid #3a3a5a;"
            " border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 10, 14, 10)
        title = QLabel("Bookmarks")
        title.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #fff; background: transparent; border: none;")
        hl.addWidget(title)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent; border: none;")
        self._list_lay = QVBoxLayout(self._content)
        self._list_lay.setContentsMargins(12, 10, 12, 10)
        self._list_lay.setSpacing(6)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, stretch=1)

    def show_at(self, global_pos, bookmarks: list, current_page: int):
        self._populate(bookmarks, current_page)
        self._reposition(global_pos)
        self.show()

    def _populate(self, bookmarks: list, current_page: int):
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not bookmarks:
            empty = QLabel("No bookmarks yet.\nUse the bookmark button to add one.")
            empty.setStyleSheet("color: #666; font-size: 9pt; background: transparent; border: none;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_lay.addWidget(empty)
        else:
            for bm in sorted(bookmarks, key=lambda b: b["page"]):
                self._list_lay.addWidget(self._make_row(bm, current_page))
        self._list_lay.addStretch()
        self._resize()

    def _make_row(self, bm: dict, current_page: int) -> QWidget:
        page = bm["page"]
        name = bm.get("name", f"Page {page + 1}")
        card = QWidget()
        card.setStyleSheet(
            "background: #2a2a4a; border: 1px solid #4a4a7a; border-radius: 6px;"
            if page == current_page else
            "background: #1e1e2e; border: 1px solid #3a3a5a; border-radius: 6px;"
        )
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(8)

        page_lbl = QLabel(f"p.{page + 1}")
        page_lbl.setFixedWidth(36)
        page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_lbl.setStyleSheet(
            "color: #3584e4; font-size: 8pt; font-weight: bold;"
            " background: transparent; border: none;"
        )
        cl.addWidget(page_lbl)

        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("Bookmark name…")
        name_edit.editingFinished.connect(
            lambda p=page, e=name_edit: self.rename_requested.emit(p, e.text())
        )
        cl.addWidget(name_edit, stretch=1)

        go_btn = QPushButton("Go")
        go_btn.setFixedWidth(36)
        go_btn.clicked.connect(lambda _, p=page: self._go(p))
        cl.addWidget(go_btn)

        del_btn = QPushButton("✕")
        del_btn.setFixedWidth(28)
        del_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #888;
                border: none; font-size: 10pt; padding: 0;
            }
            QPushButton:hover { color: #e74c3c; background: transparent; }
        """)
        del_btn.clicked.connect(lambda _, p=page: self.remove_requested.emit(p))
        cl.addWidget(del_btn)
        return card

    def _go(self, page: int):
        self.navigate.emit(page)
        self.hide()

    def _resize(self):
        self._content.adjustSize()
        h = min(self._content.sizeHint().height() + 56, self.maximumHeight())
        self.resize(self.width(), max(h, 120))

    def _reposition(self, pos):
        screen = QGuiApplication.screenAt(pos)
        sg = screen.availableGeometry() if screen \
             else QGuiApplication.primaryScreen().availableGeometry()
        x = pos.x() - self.width() // 2
        y = pos.y() + 8
        if x + self.width()  > sg.right():  x = sg.right()  - self.width()  - 4
        if x < sg.left():                   x = sg.left()   + 4
        if y + self.height() > sg.bottom(): y = pos.y() - self.height() - 8
        self.move(x, y)


# ─── Thumbnail Strip ──────────────────────────────────────────────────────────

class ThumbnailList(QListWidget):
    page_selected = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setFixedWidth(145)
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

        self.setWindowTitle("Tako Reader — タコReader")
        self.resize(1280, 900)
        self.setMinimumSize(640, 480)

        self._pages: list[QPixmap]         = []
        self._current                      = 0
        self._ocr_worker: OCRWorker | None = None
        self._settings                     = QSettings("TakoReader", "TakoReaderJP")
        self._reading_mode                 = "rtl"
        self._current_file                 = ""
        self._page_mode                    = "single"  # "single" | "double"

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._apply_dark_theme()
        self._restore_settings()
        self.setAcceptDrops(True)
        # Pass settings to OCR panel so DictPopup has access to Anki config
        self.ocr_panel.set_settings(self._settings)
        # Bookmark state
        self._bookmarks: list[dict] = []
        self._bookmark_popup = BookmarkPopup(self)
        self._bookmark_popup.navigate.connect(self.go_to_page)

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Use native QMainWindow menu bar
        self.main_menu = self.menuBar()
        self.main_menu.setObjectName("MainMenuBar")

        # Central widget holds toolbar placeholder + content
        central = QWidget()
        self.setCentralWidget(central)
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)

        # Toolbar widget is inserted here by _build_toolbar (called from __init__)
        self._outer_lay = central_lay

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
        self.scroll.setWidgetResizable(False)
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

        central_lay.addWidget(content, stretch=1)
        self.statusBar().showMessage("Open a file to begin (File → Open)  🐙")

    def _build_nav_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background: #141414; border-top: 1px solid #2a2a2a;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)

        nav_btn_style = """
            QPushButton {
                background: #2a2a2a; color: #ddd;
                border-radius: 6px; padding: 0 14px; font-size: 10pt;
            }
            QPushButton:hover    { background: #3584e4; }
            QPushButton:disabled { color: #555; }
        """

        def _nav_btn(label, slot, icon_name=None):
            b = QPushButton()
            b.setFixedHeight(32)
            b.setStyleSheet(nav_btn_style)
            b.clicked.connect(slot)
            ic = load_icon(icon_name) if icon_name else QIcon()
            if not ic.isNull():
                b.setIcon(ic)
                b.setIconSize(QSize(16, 16))
            else:
                b.setText(label)
            return b

        self.btn_first = _nav_btn("⏮", lambda: self.go_to_page(0),             "nav-first")
        self.btn_prev  = _nav_btn("◀  Prev", self.prev_page,                    "nav-prev")
        self.btn_next  = _nav_btn("Next  ▶", self.next_page,                    "nav-next")
        self.btn_last  = _nav_btn("⏭", lambda: self.go_to_page(len(self._pages) - 1), "nav-last")

        # Stacked widget: index 0 = label, index 1 = editor
        from PyQt6.QtWidgets import QStackedWidget
        self._page_nav_stack = QStackedWidget()
        self._page_nav_stack.setFixedWidth(90)

        self.page_label = QLabel("— / —")
        self.page_label.setStyleSheet("color: #aaa; font-size: 10pt;")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setToolTip("Click to jump to page")
        self.page_label.mousePressEvent = lambda _: self._start_page_jump()

        self.page_edit = QLineEdit()
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.setStyleSheet("""
            QLineEdit {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #3584e4; border-radius: 4px;
                font-size: 10pt; padding: 2px;
            }
        """)
        self.page_edit.returnPressed.connect(self._commit_page_jump)
        self.page_edit.installEventFilter(self)

        self._page_nav_stack.addWidget(self.page_label)   # index 0
        self._page_nav_stack.addWidget(self.page_edit)     # index 1

        lay.addWidget(self.btn_first)
        lay.addWidget(self.btn_prev)
        lay.addStretch()
        lay.addWidget(self._page_nav_stack)
        lay.addStretch()
        lay.addWidget(self.btn_next)
        lay.addWidget(self.btn_last)
        return bar

    def _build_menu(self):
        mb = self.main_menu

        # ── Tako Reader app menu ──
        app_menu = mb.addMenu("Tako Reader")
        about_act = QAction("About Tako Reader", self)
        about_act.triggered.connect(self._show_about)
        prefs_act = QAction("Preferences…", self, shortcut="Ctrl+,")
        prefs_act.triggered.connect(self.open_settings)
        app_menu.addAction(about_act)
        app_menu.addSeparator()
        app_menu.addAction(prefs_act)
        app_menu.addSeparator()
        quit_act_app = QAction("Quit", self, shortcut="Ctrl+Q")
        quit_act_app.triggered.connect(self.close)
        app_menu.addAction(quit_act_app)

        file_menu = mb.addMenu("File")
        open_act  = QAction("Open…",        self, shortcut="Ctrl+O")
        open_act.triggered.connect(self.open_file)
        open_dir  = QAction("Open Folder…", self)
        open_dir.triggered.connect(self.open_folder)
        close_act = QAction("Close",        self, shortcut="Ctrl+W")
        close_act.triggered.connect(self.close_file)
        file_menu.addActions([open_act, open_dir])
        file_menu.addSeparator()
        file_menu.addAction(close_act)

        view_menu = mb.addMenu("View")
        fit_w     = QAction("Fit Width",  self, shortcut="W")
        fit_w.triggered.connect(lambda: self.page_view.set_fit_mode("fit_width"))
        fit_p     = QAction("Fit Page",   self, shortcut="F")
        fit_p.triggered.connect(lambda: self.page_view.set_fit_mode("fit_page"))
        zoom_in   = QAction("Zoom In",   self, shortcut="Ctrl+=")
        zoom_in.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale * 1.2))
        zoom_out  = QAction("Zoom Out",  self, shortcut="Ctrl+-")
        zoom_out.triggered.connect(lambda: self.page_view.set_scale(self.page_view._scale / 1.2))

        self.act_thumbnails = QAction("Show Thumbnails", self, checkable=True, checked=True,
                                       shortcut="Ctrl+Shift+T")
        self.act_thumbnails.triggered.connect(self._toggle_thumbnails)
        self.act_ocr_panel  = QAction("Show OCR Panel",  self, checkable=True, checked=True,
                                       shortcut="Ctrl+Shift+P")
        self.act_ocr_panel.triggered.connect(self._toggle_ocr_panel)

        rtl_act = QAction("RTL (Manga)", self, checkable=True, checked=True)
        rtl_act.triggered.connect(lambda v: self._set_reading_mode("rtl" if v else "ltr"))

        view_menu.addActions([fit_w, fit_p, zoom_in, zoom_out])
        view_menu.addSeparator()
        single_act = QAction("Single Page", self, checkable=True, checked=True)
        double_act = QAction("Double Page", self, checkable=True)
        single_act.triggered.connect(lambda: (self._set_page_mode("single"),
                                              single_act.setChecked(True),
                                              double_act.setChecked(False)))
        double_act.triggered.connect(lambda: (self._set_page_mode("double"),
                                              double_act.setChecked(True),
                                              single_act.setChecked(False)))
        self._menu_single_act = single_act
        self._menu_double_act = double_act
        view_menu.addActions([single_act, double_act])
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
        jump_act = QAction("Jump to Page…", self, shortcut="Ctrl+G")
        jump_act.triggered.connect(self._start_page_jump)
        nav_menu.addAction(jump_act)
        nav_menu.addSeparator()
        bm_toggle = QAction("Toggle Bookmark", self, shortcut="Ctrl+B")
        bm_toggle.triggered.connect(self._toggle_bookmark)
        bm_list   = QAction("Show Bookmarks…", self, shortcut="Ctrl+Shift+B")
        bm_list.triggered.connect(self._show_bookmarks_popup)
        nav_menu.addActions([bm_toggle, bm_list])

        ocr_menu = mb.addMenu("OCR")
        self.act_ocr_mode = QAction("OCR Selection Mode", self,
                                    shortcut="Ctrl+Shift+O", checkable=True)
        self.act_ocr_mode.triggered.connect(self._toggle_ocr_mode)
        ocr_menu.addAction(self.act_ocr_mode)
        check_ocr = QAction("Check OCR Installation…", self)
        check_ocr.triggered.connect(self._check_ocr)
        ocr_menu.addAction(check_ocr)
        ocr_menu.addSeparator()
        dict_act = QAction("Dictionary Lookup", self, shortcut="Ctrl+D")
        dict_act.triggered.connect(lambda: self.ocr_panel.lookup_shortcut())
        ocr_menu.addAction(dict_act)



    def _build_toolbar(self) -> QWidget:
        """Returns a plain QWidget toolbar that slots into the outer VBox layout."""
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
                padding: 4px 8px; font-size: 10pt;
            }
            QPushButton:hover   { background: #2e2e2e; color: #fff; }
            QPushButton:checked { background: #3584e4; color: #fff; }
        """

        def _btn(label, slot, checkable=False, icon_name=None, tooltip=None):
            b = QPushButton(label)
            b.setCheckable(checkable)
            b.setStyleSheet(btn_style)
            b.clicked.connect(slot)
            if icon_name:
                ic = load_icon(icon_name)
                if not ic.isNull():
                    b.setIcon(ic)
                    b.setIconSize(QSize(16, 16))
                    b.setText("")
            if tooltip:
                b.setToolTip(tooltip)
            return b

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setStyleSheet("color: #333;")
            f.setFixedWidth(10)
            return f

        # ── Left side: thumbnails toggle ──
        self.tb_thumb_btn = _btn("", self._toggle_thumbnails, checkable=True,
                                 tooltip="Toggle Thumbnails (Ctrl+Shift+T)")
        self.tb_thumb_btn.setChecked(True)
        lay.addWidget(self.tb_thumb_btn)
        lay.addWidget(_sep())

        # ── Centre tools ──
        lay.addWidget(_btn("📂 Open", self.open_file,
                           icon_name="open", tooltip="Open file (Ctrl+O)"))
        lay.addWidget(_sep())
        lay.addWidget(_btn("↔ Fit Width",
                           lambda: self.page_view.set_fit_mode("fit_width"),
                           icon_name="fit-width", tooltip="Fit Width (W)"))
        lay.addWidget(_btn("⬜ Fit Page",
                           lambda: self.page_view.set_fit_mode("fit_page"),
                           icon_name="fit-page", tooltip="Fit Page (F)"))
        lay.addWidget(_sep())
        lay.addWidget(_btn("🔍+",
                           lambda: self.page_view.set_scale(self.page_view._scale * 1.2),
                           icon_name="zoom-in", tooltip="Zoom In (Ctrl+=)"))
        lay.addWidget(_btn("🔍−",
                           lambda: self.page_view.set_scale(self.page_view._scale / 1.2),
                           icon_name="zoom-out", tooltip="Zoom Out (Ctrl+-)"))
        lay.addWidget(_sep())
        self.ocr_btn = _btn("🔤 OCR Mode", self._toggle_ocr_mode, checkable=True,
                            icon_name="ocr", tooltip="OCR Selection Mode (Ctrl+Shift+O)")
        lay.addWidget(self.ocr_btn)

        # ── Right side: bookmarks + OCR panel toggle ──
        lay.addStretch()
        page_mode_combo = QComboBox()
        page_mode_combo.addItem("Single Page", "single")
        page_mode_combo.addItem("Double Page", "double")
        page_mode_combo.setFixedWidth(110)
        page_mode_combo.setStyleSheet("""
            QComboBox {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 2px 6px; font-size: 9pt;
            }
            QComboBox::drop-down {
                border-left: 1px solid #444; width: 20px;
                border-top-right-radius: 4px; border-bottom-right-radius: 4px;
            }
            QComboBox::down-arrow {
                image: none; width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #888;
            }
            QComboBox::drop-down:hover { background: #3a3a4a; }
            QComboBox::down-arrow:hover { border-top-color: #ddd; }
            QComboBox QAbstractItemView {
                background: #252525; color: #ddd;
                selection-background-color: #3584e4;
            }
        """)
        page_mode_combo.currentIndexChanged.connect(
            lambda: self._set_page_mode(page_mode_combo.currentData())
        )
        self._page_mode_combo = page_mode_combo
        lay.addWidget(page_mode_combo)
        lay.addWidget(_sep())
        lay.addWidget(_sep())
        self.tb_bookmark_btn = _btn("", self._toggle_bookmark, checkable=True,
                                    icon_name="bookmark-off",
                                    tooltip="Bookmark this page (Ctrl+B)")
        lay.addWidget(self.tb_bookmark_btn)
        self.tb_bookmarks_btn = _btn("", self._show_bookmarks_popup,
                                     icon_name="bookmarks",
                                     tooltip="Show all bookmarks (Ctrl+Shift+B)")
        lay.addWidget(self.tb_bookmarks_btn)
        lay.addWidget(_sep())
        self.tb_ocr_btn = _btn("", self._toggle_ocr_panel, checkable=True,
                               tooltip="Toggle OCR Panel (Ctrl+Shift+P)")
        self.tb_ocr_btn.setChecked(True)
        lay.addWidget(self.tb_ocr_btn)

        # Insert at top of central layout (index 0), above content area
        self._outer_lay.insertWidget(0, bar)

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

    def close_file(self):
        """Close the current file and return to blank state."""
        self._pages        = []
        self._current      = 0
        self._current_file = ""
        self._bookmarks    = []
        self.page_view.set_pixmap(QPixmap())
        self.thumb_list.clear()
        self.page_label.setText("— / —")
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_first.setEnabled(False)
        self.btn_last.setEnabled(False)
        self.ocr_panel.clear_all()
        self.setWindowTitle("Tako Reader — タコReader")
        self.statusBar().showMessage("File closed.")

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

        self.setWindowTitle(f"Tako Reader — {Path(path).name}")
        self._current_file = str(Path(path).resolve())
        self._bookmarks    = self._load_bookmarks()
        if self._settings.value("ocr/clear_on_file", True, type=bool):
            self.ocr_panel.clear_all()

        self.thumb_list.load_pages(pages)
        self._settings.setValue("session/last_file", str(Path(path).resolve()))
        self.go_to_page(0)
        self.statusBar().showMessage(f"Loaded {len(pages)} pages — {Path(path).name}")

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

    def _get_display_pixmap(self, index: int) -> QPixmap:
        """Return a single or side-by-side double-page pixmap."""
        px1 = self._pages[index]
        if self._page_mode != "double" or index + 1 >= len(self._pages):
            return px1
        px2 = self._pages[index + 1]
        # Stitch: in RTL mode page order is right-to-left
        left, right = (px2, px1) if self._reading_mode == "rtl" else (px1, px2)
        h = max(left.height(), right.height())
        combined = QPixmap(left.width() + right.width(), h)
        combined.fill(Qt.GlobalColor.transparent)
        painter = QPainter(combined)
        painter.drawPixmap(0,            (h - left.height())  // 2, left)
        painter.drawPixmap(left.width(), (h - right.height()) // 2, right)
        painter.end()
        return combined

    def go_to_page(self, index: int):
        if not self._pages:
            return
        index = max(0, min(index, len(self._pages) - 1))
        self._current = index
        self.page_view.set_pixmap(self._get_display_pixmap(index))
        self.thumb_list.select_page(index)
        self.page_label.setText(f"{index+1} / {len(self._pages)}")
        self.btn_prev.setEnabled(index > 0)
        self.btn_next.setEnabled(index < len(self._pages) - 1)
        self.btn_first.setEnabled(index > 0)
        self.btn_last.setEnabled(index < len(self._pages) - 1)
        self._save_session_page(index)
        if hasattr(self, "tb_bookmark_btn"):
            self._update_bookmark_btn()

    def prev_page(self):
        step = 2 if self._page_mode == "double" else 1
        self.go_to_page(self._current + (step if self._reading_mode == "rtl" else -step))

    def next_page(self):
        step = 2 if self._page_mode == "double" else 1
        self.go_to_page(self._current + (-step if self._reading_mode == "rtl" else step))

    def _toggle_thumbnails(self, checked: bool | None = None):
        if checked is None:
            checked = not self.thumb_list.isVisible()
        self.thumb_list.setVisible(checked)
        self.act_thumbnails.setChecked(checked)
        self.tb_thumb_btn.setChecked(checked)
        ic = load_icon("panel-thumbnails-hide" if checked else "panel-thumbnails-show")
        if not ic.isNull():
            self.tb_thumb_btn.setIcon(ic)
        else:
            self.tb_thumb_btn.setText("‹‹" if checked else "››")

    def _toggle_ocr_panel(self, checked: bool | None = None):
        if checked is None:
            checked = not self.ocr_panel.isVisible()
        self.ocr_panel.setVisible(checked)
        self.act_ocr_panel.setChecked(checked)
        self.tb_ocr_btn.setChecked(checked)
        ic = load_icon("panel-ocr-hide" if checked else "panel-ocr-show")
        if not ic.isNull():
            self.tb_ocr_btn.setIcon(ic)
        else:
            self.tb_ocr_btn.setText("‹‹" if checked else "››")

    def _set_page_mode(self, mode: str):
        self._page_mode = mode
        if self._pages:
            self.go_to_page(self._current)
        self.statusBar().showMessage(f"Page mode: {mode.capitalize()}", 2000)
        # Sync toolbar combo
        if hasattr(self, "_page_mode_combo"):
            idx = 1 if mode == "double" else 0
            self._page_mode_combo.blockSignals(True)
            self._page_mode_combo.setCurrentIndex(idx)
            self._page_mode_combo.blockSignals(False)
        # Sync menu actions
        if hasattr(self, "_menu_single_act"):
            self._menu_single_act.setChecked(mode == "single")
            self._menu_double_act.setChecked(mode == "double")

    def _start_page_jump(self):
        """Switch page label to edit mode."""
        if not self._pages:
            return
        self.page_edit.setText(str(self._current + 1))
        self.page_edit.selectAll()
        self._page_nav_stack.setCurrentIndex(1)
        self.page_edit.setFocus()

    def _commit_page_jump(self):
        """Parse the entered page number, jump, restore label."""
        text = self.page_edit.text().strip()
        self._page_nav_stack.setCurrentIndex(0)
        self.page_label.setFocus()
        if not text or not self._pages:
            return
        try:
            target = int(text) - 1  # convert 1-based to 0-based
        except ValueError:
            return
        # Clamp: out-of-range goes to first or last
        target = max(0, min(target, len(self._pages) - 1))
        self.go_to_page(target)

    def eventFilter(self, obj, event):
        """Pressing Escape while the page edit is open cancels the jump."""
        from PyQt6.QtCore import QEvent
        if obj is self.page_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._page_nav_stack.setCurrentIndex(0)
                return True
            if event.type() == QEvent.Type.FocusOut:
                self._page_nav_stack.setCurrentIndex(0)
        return super().eventFilter(obj, event)

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
        device = self._settings.value("ocr/device", "cpu")
        # If the process isn't alive yet, this is a lazy first load
        mgr = OCRProcessManager.get(device)
        if not mgr.is_alive():
            self.ocr_panel.set_ocr_state("loading")
        self.ocr_panel.set_status(f"⏳ Running OCR on {device}…")
        self._ocr_worker = OCRWorker(image, rect, device=device)
        self._ocr_worker.result_ready.connect(self.ocr_panel.set_text)
        self._ocr_worker.result_ready.connect(
            lambda _: self.ocr_panel.set_ocr_state("ready")
        )
        self._ocr_worker.error_occurred.connect(self.ocr_panel.set_status)
        self._ocr_worker.error_occurred.connect(
            lambda _: self.ocr_panel.set_ocr_state("error")
        )
        self._ocr_worker.start()

    def _show_about(self):
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        msg = QMessageBox(self)
        msg.setWindowTitle("About Tako Reader")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "<h2>Tako Reader — タコReader</h2>"
            "<p>A Japanese manga reader with built-in OCR, dictionary lookup, "
            "and Anki integration for language immersion.</p>"
            "<table cellspacing='4'>"
            "<tr><td><b>Author</b></td><td>Tacoccino</td></tr>"
            f"<tr><td><b>Qt</b></td><td>{QT_VERSION_STR}</td></tr>"
            f"<tr><td><b>PyQt</b></td><td>{PYQT_VERSION_STR}</td></tr>"
            "<tr><td><b>Source</b></td>"
            "<td><a href='https://github.com/tacoccino/tako-reader'>"
            "github.com/tacoccino/tako-reader</a></td></tr>"
            "</table>"
            "<p><small>Dictionary data: JMdict / KANJIDIC2 © Electronic Dictionary "
            "Research and Development Group (CC BY-SA 3.0)<br>"
            "OCR: manga-ocr by Maciej Budyś</small></p>"
        )
        msg.setStyleSheet("QMessageBox { background: #1a1a1a; color: #e0e0e0; }"
                          "QLabel { color: #e0e0e0; }")
        msg.exec()

    def open_settings(self):
        dlg = SettingsDialog(self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Restart any cached OCR process so next call uses the new device
            new_device = self._settings.value("ocr/device", "cpu")
            for dev in list(OCRProcessManager._instances.keys()):
                if dev != new_device:
                    OCRProcessManager._instances[dev]._stop()
                    del OCRProcessManager._instances[dev]
            self.statusBar().showMessage(f"Settings saved — OCR device: {new_device}")

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

            QMenuBar                      { background: #1a1a1a; color: #ddd;
                                            border-bottom: 1px solid #2a2a2a; }
            QMenuBar::item:selected       { background: #3584e4; }

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
    # ─────────────────────────────────────────────────────────────────────────
    # Bookmarks
    # ─────────────────────────────────────────────────────────────────────────

    def _bm_key(self) -> str:
        import hashlib
        h = hashlib.md5(self._current_file.encode()).hexdigest()[:12]
        return f"bookmarks/{h}"

    def _load_bookmarks(self) -> list:
        if not self._current_file:
            return []
        import json as _json
        raw = self._settings.value(self._bm_key(), "[]")
        try:
            return _json.loads(raw)
        except Exception:
            return []

    def _save_bookmarks(self):
        import json as _json
        self._settings.setValue(self._bm_key(), _json.dumps(self._bookmarks))

    def _page_is_bookmarked(self) -> bool:
        return any(b["page"] == self._current for b in self._bookmarks)

    def _toggle_bookmark(self):
        if not self._pages:
            return
        if self._page_is_bookmarked():
            self._bookmarks = [b for b in self._bookmarks
                               if b["page"] != self._current]
        else:
            self._bookmarks.append({
                "page": self._current,
                "name": f"Page {self._current + 1}",
            })
        self._save_bookmarks()
        self._update_bookmark_btn()

    def _update_bookmark_btn(self):
        on = self._page_is_bookmarked()
        self.tb_bookmark_btn.setChecked(on)
        ic = load_icon("bookmark-on" if on else "bookmark-off")
        if not ic.isNull():
            self.tb_bookmark_btn.setIcon(ic)
            self.tb_bookmark_btn.setIconSize(QSize(16, 16))

    def _show_bookmarks_popup(self):
        btn_geo = self.tb_bookmarks_btn.geometry()
        btn_pos = self.tb_bookmarks_btn.mapToGlobal(
            QPoint(btn_geo.width() // 2, btn_geo.height())
        )
        self._bookmark_popup.rename_requested.connect(self._on_bookmark_rename)
        self._bookmark_popup.remove_requested.connect(self._on_bookmark_remove)
        self._bookmark_popup.show_at(btn_pos, list(self._bookmarks), self._current)

    def _on_bookmark_rename(self, page: int, name: str):
        for bm in self._bookmarks:
            if bm["page"] == page:
                bm["name"] = name
                break
        self._save_bookmarks()

    def _on_bookmark_remove(self, page: int):
        self._bookmarks = [b for b in self._bookmarks if b["page"] != page]
        self._save_bookmarks()
        self._update_bookmark_btn()
        btn_geo = self.tb_bookmarks_btn.geometry()
        btn_pos = self.tb_bookmarks_btn.mapToGlobal(
            QPoint(btn_geo.width() // 2, btn_geo.height())
        )
        self._bookmark_popup.show_at(btn_pos, list(self._bookmarks), self._current)

    # Settings persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _restore_settings(self):
        geo = self._settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

        # Panel visibility
        thumb_vis = self._settings.value("ui/thumb_visible", True,  type=bool)
        ocr_vis   = self._settings.value("ui/ocr_visible",   True,  type=bool)
        self._toggle_thumbnails(thumb_vis)
        self._toggle_ocr_panel(ocr_vis)

        # Page mode
        page_mode = self._settings.value("ui/page_mode", "single")
        self._set_page_mode(page_mode)

        # Segment toggle
        seg_on = self._settings.value("ui/segment_on", False, type=bool)
        self.ocr_panel.seg_check.setChecked(seg_on)
        # Manually fire the toggle so internal state syncs
        self.ocr_panel._on_seg_toggled()

    def _maybe_warmup_ocr(self):
        """Start the OCR model in the background if the user has opted in."""
        if not self._settings.value("ocr/warmup", False, type=bool):
            return
        device = self._settings.value("ocr/device", "cpu")
        self.statusBar().showMessage("⏳ Pre-loading OCR model…")
        self.ocr_panel.set_ocr_state("loading")
        self._warmup_worker = OCRWarmupWorker(device)
        self._warmup_worker.ready.connect(self._on_ocr_ready)
        self._warmup_worker.failed.connect(self._on_ocr_failed)
        self._warmup_worker.start()

    def _on_ocr_ready(self, dev: str):
        self.ocr_panel.set_ocr_state("ready")
        self.statusBar().showMessage(f"✓ OCR model ready on {dev}  🐙", 4000)

    def _on_ocr_failed(self, err: str):
        self.ocr_panel.set_ocr_state("error")
        self.statusBar().showMessage(f"⚠ OCR warmup failed: {err}", 6000)

    def _save_session_page(self, index: int):
        """Persist the current page index (only when session memory is on)."""
        if self._settings.value("general/session_memory", True, type=bool):
            self._settings.setValue("session/last_page", index)

    def _restore_session(self):
        """Re-open the last file and jump to the last page, if session memory is on."""
        if not self._settings.value("general/session_memory", True, type=bool):
            return
        last_file = self._settings.value("session/last_file", "")
        last_page = self._settings.value("session/last_page", 0, type=int)
        if last_file and Path(last_file).exists():
            self._load_path(last_file)
            # go_to_page(0) was called by _load_path; now jump to actual last page
            if last_page > 0:
                self.go_to_page(last_page)
            self.statusBar().showMessage(
                f"Restored session: {Path(last_file).name}  —  page {last_page + 1}"
            )

    def closeEvent(self, event):
        self._settings.setValue("geometry",          self.saveGeometry())
        self._settings.setValue("ui/thumb_visible",  self.thumb_list.isVisible())
        self._settings.setValue("ui/ocr_visible",    self.ocr_panel.isVisible())
        self._settings.setValue("ui/segment_on",     self.ocr_panel.seg_check.isChecked())
        self._settings.setValue("ui/page_mode",      self._page_mode)
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
        app.setStyleSheet("""
            QToolTip {
                background: #2a2a3a;
                color: #e0e0e0;
                border: 1px solid #5a5a8a;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 9pt;
            }
        """)

        dlog("creating TakoReader window...")
        window = TakoReader()

        dlog("calling window.show()...")
        window.show()
        dlog("window shown, entering event loop")

        if len(argv) > 1:
            dlog(f"loading file: {argv[1]}")
            window._load_path(argv[1])
        else:
            # No file passed on CLI — try to restore last session
            window._restore_session()

        # Warm up OCR in background if opted in, after a short delay so
        # the status bar message from session restore shows first
        QTimer.singleShot(500, window._maybe_warmup_ocr)

        sys.exit(app.exec())

    except Exception:
        print("[tako] FATAL EXCEPTION:")
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()
