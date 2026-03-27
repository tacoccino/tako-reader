"""
Tako Reader — dictionary lookup and popup.
Offline JMdict / KANJIDIC2 lookup via jamdict, and the floating
popup widget that shows results with Anki integration.
"""

import webbrowser
from urllib.parse import quote as url_quote

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QApplication,
)
from PyQt6.QtCore import Qt, QPoint, QSettings, QTimer
from PyQt6.QtGui import QFont, QGuiApplication

from utils import _ctrl
from anki import (
    make_furigana_html, anki_store_media,
    AnkiAddWorker, AnkiEditDialog,
)


# ─── Dictionary lookup ──────────────────────────────────────────────────────

def lookup_word(word: str) -> list[dict]:
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


# ─── Dictionary popup ───────────────────────────────────────────────────────

class DictPopup(QWidget):
    """
    Floating frameless popup showing dictionary info for a Japanese word.
    Dismisses on click outside.
    """

    def __init__(self, app_settings: QSettings, main_window=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.app_settings  = app_settings
        self.main_window   = main_window
        self._current_sentence = ""
        self._add_workers: set = set()
        self._connect_worker = None
        self._fields_worker  = None
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

    def show_word(self, word: str, global_pos: QPoint, sentence: str = ""):
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
        entries = lookup_word(word)

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

            header_row = QHBoxLayout()
            header_row.setSpacing(8)
            word_lbl = QLabel(entry["word"])
            word_lbl.setFont(QFont("Noto Serif JP, serif", 20, QFont.Weight.Bold))
            word_lbl.setStyleSheet("color: #ffffff; border: none; background: transparent;")
            word_lbl.setWordWrap(True)
            header_row.addWidget(word_lbl, stretch=1)

            anki_btn = QPushButton("+ Anki")
            anki_btn.setStyleSheet(anki_btn_style)
            anki_btn.setToolTip(f"+ Anki  |  {_ctrl()}+click to edit before adding")
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

            if entry["senses"]:
                self._add_label("Definitions", size=8, colour="#666", bold=True)
                for j, sense in enumerate(entry["senses"][:6]):
                    self._add_label(f"{j+1}.  {sense}", size=9, indent=True)

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
                           definition: str, btn: QPushButton):
        modifiers = QApplication.keyboardModifiers()
        ctrl = Qt.KeyboardModifier.ControlModifier
        if modifiers & ctrl:
            self._open_anki_edit_dialog(word, reading, definition)
            return
        mw = self.main_window
        if mw and mw._image_field_is_mapped():
            self.hide()
            def _on_image(b64, w=word, r=reading, d=definition):
                self.show()
                self._add_to_anki(w, r, d, image_override=b64)
            mw.enter_marquee_mode(_on_image)
        else:
            self._add_to_anki(word, reading, definition)

    def _open_anki_edit_dialog(self, word: str, reading: str,
                               definition: str, image_b64: str = ""):
        dlg = AnkiEditDialog(
            word, reading, definition,
            self._current_sentence,
            self.app_settings,
            image_b64=image_b64,
            main_window=self.main_window,
            parent=self
        )
        def _on_accepted():
            d = dlg.get_values()
            self._add_to_anki(d["word"], d["reading"], d["definition"],
                              sentence_override=d["sentence"],
                              image_override=d.get("image", ""))
        dlg.accepted.connect(_on_accepted)
        dlg.setModal(False)
        dlg.show()

    def _add_to_anki(self, word: str, reading: str, definition: str,
                     sentence_override: str | None = None,
                     image_override: str = ""):
        s = self.app_settings
        url   = s.value("anki/url",   "http://localhost:8765")
        key   = s.value("anki/key",   "")
        deck  = s.value("anki/deck",  "")
        model = s.value("anki/model", "")

        if not deck or not model:
            self._show_toast("⚠ Configure Anki in Settings first.")
            return

        furigana = make_furigana_html(word, reading)
        source_map = {
            "Word":       word,
            "Reading":    reading,
            "Furigana":   furigana,
            "Definition": definition,
            "Sentence":   sentence_override if sentence_override is not None else self._current_sentence,
            "Image":      image_override or "",
        }
        fields = {}
        s.beginGroup("anki/field")
        for field_name in s.childKeys():
            source = s.value(field_name, "— skip —")
            if source == "— skip —" or source not in source_map:
                continue
            if source == "Image":
                b64 = source_map["Image"]
                if b64:
                    import time
                    image_filename = f"tako_{int(time.time()*1000)}.png"
                    try:
                        anki_store_media(url, key, image_filename, b64)
                        fields[field_name] = f'<img src="{image_filename}">'
                    except Exception:
                        pass
            else:
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
        toast = QLabel(message, self)
        toast.setStyleSheet("""
            QLabel {
                background: #2a2a3a; color: #eee;
                border: 1px solid #5a5a8a; border-radius: 6px;
                padding: 6px 12px; font-size: 9pt;
            }
        """)
        toast.adjustSize()
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

    def _reposition(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos)
        if screen:
            sg = screen.availableGeometry()
        else:
            sg = QGuiApplication.primaryScreen().availableGeometry()

        x = global_pos.x() + 12
        y = global_pos.y() + 12

        if x + self.width() > sg.right():
            x = global_pos.x() - self.width() - 12
        if y + self.height() > sg.bottom():
            y = global_pos.y() - self.height() - 12

        self.move(x, y)
