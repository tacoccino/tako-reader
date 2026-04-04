"""
Tako Reader — Anki integration.
AnkiConnect helpers, background workers, furigana generation,
and the card-edit dialog.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTextEdit,
    QPushButton, QDialogButtonBox, QFileDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QPixmap, QFont

from utils import load_icon
import theme


# ─── Furigana helper ─────────────────────────────────────────────────────────

def make_furigana_html(word: str, reading: str) -> str:
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
        kks   = pykakasi.kakasi()
        items = kks.convert(word)
        parts = []
        for item in items:
            orig = item.get("orig", "")
            hira = item.get("hira", "")
            has_kanji = any("一" <= c <= "鿿" for c in orig)
            if has_kanji and hira and hira != orig:
                parts.append(f"<ruby>{orig}<rt>{hira}</rt></ruby>")
            else:
                parts.append(orig)
        return "".join(parts)
    except Exception:
        if reading and reading != word:
            return f"{word}[{reading}]"
        return word


# ─── AnkiConnect helpers ─────────────────────────────────────────────────────

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


def anki_store_media(url: str, api_key: str,
                     filename: str, data_b64: str) -> str:
    """
    Store a media file in Anki via AnkiConnect storeMediaFile.
    Returns the filename Anki used (same as passed in).
    """
    _anki_request("storeMediaFile", url, api_key,
                  filename=filename, data=data_b64)
    return filename


# ─── Background workers ─────────────────────────────────────────────────────

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


# ─── Anki edit dialog ───────────────────────────────────────────────────────

class AnkiEditDialog(QDialog):
    """
    Shown when the user Ctrl/Cmd-clicks "+ Anki".
    Lets them edit word, reading, furigana, definition, and sentence
    before the card is added.
    """

    def __init__(self, word: str, reading: str, definition: str,
                 sentence: str, app_settings: QSettings,
                 image_b64: str = "", main_window=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Card — Tako Reader")
        self.setMinimumWidth(420)
        self._image_b64   = image_b64
        self._main_window = main_window
        self.setStyleSheet(f"""
            QDialog  {{ background: {theme._active['window_bg']}; color: {theme._active['text']}; }}
            QLabel   {{ color: {theme._active['text_secondary']}; font-size: 9pt; }}
            QTextEdit, QLineEdit {{
                background: {theme._active['input_bg']}; color: {theme._active['text']};
                border: 1px solid {theme._active['border_light']}; border-radius: 4px;
                padding: 4px 6px; font-size: 10pt;
            }}
            QPushButton {{
                background: {theme._active['input_bg']}; color: {theme._active['text']};
                border: 1px solid {theme._active['border_light']}; border-radius: 5px;
                padding: 5px 18px; font-size: 10pt;
            }}
            QPushButton:hover {{ background: {theme.ACCENT}; color: #fff; border-color: {theme.ACCENT}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        furigana_html = make_furigana_html(word, reading)

        self._editors: dict[str, QWidget] = {}
        for label, value, multiline in [
            ("Word",       word,          False),
            ("Reading",    reading,       False),
            ("Furigana",   furigana_html, False),
            ("Definition", definition,    True),
            ("Sentence",   sentence,      True),
        ]:
            lbl = QLabel(label)
            root.addWidget(lbl)
            if multiline:
                w = QTextEdit()
                w.setPlainText(value)
                w.setFixedHeight(72)
            else:
                w = QLineEdit(value)
            root.addWidget(w)
            self._editors[label] = w

        # Image field — only shown if main_window is available
        if main_window is not None:
            img_lbl = QLabel("Image")
            root.addWidget(img_lbl)
            self._img_status = QLabel("No image selected")
            self._img_status.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 9pt;")
            root.addWidget(self._img_status)

            self._img_preview = QLabel()
            self._img_preview.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self._img_preview.hide()
            root.addWidget(self._img_preview)

            _btn_style = f"""
                QPushButton {{
                    background: {theme._active['input_bg']}; color: {theme._active['text_secondary']};
                    border: 1px solid {theme._active['border_light']}; border-radius: 4px;
                    padding: 4px 10px; font-size: 9pt;
                }}
                QPushButton:hover {{ background: {theme.ACCENT}; color: #fff; }}
            """
            _flat_icon_btn = f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 4px; padding: 4px;
                }}
                QPushButton:hover {{ background: {theme._active['hover_bg']}; }}
            """
            img_btns = QHBoxLayout()
            img_btns.setSpacing(4)

            sel_btn = QPushButton("Select Region…")
            sel_btn.setStyleSheet(_btn_style)
            sel_btn.clicked.connect(self._select_image)
            img_btns.addWidget(sel_btn)

            upload_btn = QPushButton()
            upload_btn.setToolTip("Upload image…")
            upload_btn.setFixedSize(26, 26)
            upload_btn.setStyleSheet(_flat_icon_btn)
            from PyQt6.QtCore import QSize
            ic_upload = load_icon("upload")
            if not ic_upload.isNull():
                upload_btn.setIcon(ic_upload)
                upload_btn.setIconSize(QSize(16, 16))
            else:
                upload_btn.setText("↑")
            upload_btn.clicked.connect(self._upload_image)
            img_btns.addWidget(upload_btn)

            clear_btn = QPushButton()
            clear_btn.setToolTip("Clear image")
            clear_btn.setFixedSize(26, 26)
            clear_btn.setStyleSheet(_flat_icon_btn)
            ic_clear = load_icon("clear-img")
            if not ic_clear.isNull():
                clear_btn.setIcon(ic_clear)
                clear_btn.setIconSize(QSize(16, 16))
            else:
                clear_btn.setText("✕")
            clear_btn.clicked.connect(lambda: self._set_image(""))
            img_btns.addWidget(clear_btn)
            img_btns.addStretch()
            root.addLayout(img_btns)

            if image_b64:
                self._set_image(image_b64)

        # Audio field
        audio_lbl = QLabel("Audio")
        root.addWidget(audio_lbl)
        audio_row = QHBoxLayout()
        audio_row.setSpacing(6)
        self._audio_edit = QLineEdit(word)
        self._audio_edit.setPlaceholderText("Word to pronounce (blank to skip)")
        self._audio_edit.setToolTip(
            "The word sent to TTS / Forvo for pronunciation.\n"
            "Change to the reading if the kanji form isn't pronounced correctly.\n"
            "Leave blank to skip audio."
        )
        audio_row.addWidget(self._audio_edit, stretch=1)

        play_btn = QPushButton("🔊")
        play_btn.setFixedSize(30, 26)
        play_btn.setToolTip("Preview pronunciation")
        play_btn.clicked.connect(self._preview_audio)
        audio_row.addWidget(play_btn)
        root.addLayout(audio_row)

        self._audio_status = QLabel("")
        self._audio_status.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 8pt;")
        root.addWidget(self._audio_status)
        self._app_settings = app_settings

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _set_image(self, b64: str):
        """Update the stored image, status label, and preview."""
        self._image_b64 = b64
        if b64:
            self._img_status.setText("✓ Image set")
            self._img_status.setStyleSheet("color: #2ecc71; font-size: 9pt;")
            try:
                import base64 as _b64
                px = QPixmap()
                px.loadFromData(_b64.b64decode(b64))
                if not px.isNull():
                    self._img_preview.setPixmap(
                        px.scaled(160, 100,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                    )
                    self._img_preview.show()
                    return
            except Exception:
                pass
        self._img_status.setText("No image selected")
        self._img_status.setStyleSheet(f"color: {theme._active['text_muted']}; font-size: 9pt;")
        self._img_preview.hide()

    def _select_image(self):
        """Hide dialog, enter marquee, restore with captured image."""
        snapshot = {}
        for key, widget in self._editors.items():
            if isinstance(widget, QTextEdit):
                snapshot[key] = widget.toPlainText()
            else:
                snapshot[key] = widget.text()
        self.hide()
        def _on_capture(b64):
            for key, val in snapshot.items():
                w = self._editors[key]
                if isinstance(w, QTextEdit):
                    w.setPlainText(val)
                else:
                    w.setText(val)
            self._set_image(b64)
            self.show()
        self._main_window.enter_marquee_mode(_on_capture)

    def _upload_image(self):
        """Open a file picker and load the chosen image as base64."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Image",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if not path:
            return
        try:
            import base64 as _b64
            with open(path, "rb") as f:
                data = f.read()
            b64 = _b64.b64encode(data).decode()
            self._set_image(b64)
        except Exception as e:
            print(f"[upload error] {e}")

    def _preview_audio(self):
        """Fetch and play pronunciation for the word in the audio field."""
        word = self._audio_edit.text().strip()
        if not word:
            self._audio_status.setText("No word to pronounce")
            return
        self._audio_status.setText("Fetching audio…")
        from audio import AudioFetchWorker, AudioPlayer
        self._audio_preview_worker = AudioFetchWorker(word, self._app_settings)
        def _on_done(w, data):
            AudioPlayer.get().play_bytes(data)
            forvo_key = self._app_settings.value("dict/forvo_key", "").strip()
            source = "Forvo" if forvo_key else "Google TTS"
            self._audio_status.setText(f"🔊 Playing ({source})")
        def _on_fail(w, err):
            self._audio_status.setText(f"🔇 {err}")
        self._audio_preview_worker.finished.connect(_on_done)
        self._audio_preview_worker.failed.connect(_on_fail)
        self._audio_preview_worker.start()

    def get_values(self) -> dict[str, str]:
        result = {}
        for key, widget in self._editors.items():
            if isinstance(widget, QTextEdit):
                result[key.lower()] = widget.toPlainText()
            else:
                result[key.lower()] = widget.text()
        result["image"] = self._image_b64
        result["audio_word"] = self._audio_edit.text().strip()
        return result
