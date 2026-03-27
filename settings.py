"""
Tako Reader — settings dialog.
Tabbed preferences window covering General, Appearance, OCR, Anki,
and customisable keyboard shortcuts.
"""

import sys
import subprocess
import json as _json

from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QGroupBox, QComboBox, QCheckBox,
    QLineEdit, QSpinBox, QTabWidget, QKeySequenceEdit,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QFont

from anki import AnkiConnectWorker, AnkiFieldsWorker

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

    def __init__(self, app_settings: QSettings, shortcut_defaults: dict = None,
                 parent=None):
        super().__init__(parent)
        self.app_settings = app_settings
        self._shortcut_defaults = shortcut_defaults or {}
        self.setWindowTitle("Tako Reader — Settings")
        self.setMinimumWidth(480)
        self.setMinimumHeight(300)
        self.setModal(True)
        self._apply_style()
        self._build_ui()
        self._load_values()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _make_tab(self) -> tuple:
        """Return (scroll_widget, inner_layout) for a tab page."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(24, 20, 24, 8)
        lay.setSpacing(20)
        scroll.setWidget(content)
        return scroll, lay

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 16)
        root.setSpacing(0)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                border-radius: 0px;
                background: #1a1a1a;
            }
            QTabBar::tab {
                min-width: 80px;
                background: #1e1e1e; color: #888;
                padding: 8px 15px; font-size: 9pt;
                border: 1px solid #2a2a2a;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected { background: #1a1a1a; color: #fff; }
            QTabBar::tab:hover:!selected { background: #252525; color: #ccc; }
        """)

        # ── General tab ──
        gen_scroll, self._content_lay = self._make_tab()
        self._build_general_section()
        self._content_lay.addStretch()
        tabs.addTab(gen_scroll, "General")

        # ── Appearance tab (placeholder) ──
        app_scroll, app_lay = self._make_tab()
        placeholder = QLabel("Appearance options coming soon.\n\n"
                             "Planned: light theme, accent colours.")
        placeholder.setStyleSheet("color: #555; font-size: 9pt;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_lay.addStretch()
        app_lay.addWidget(placeholder)
        app_lay.addStretch()
        tabs.addTab(app_scroll, "Appearance")

        # ── OCR tab ──
        ocr_scroll, self._content_lay = self._make_tab()
        self._build_ocr_section()
        self._content_lay.addStretch()
        tabs.addTab(ocr_scroll, "OCR")

        # ── Anki tab ──
        anki_scroll, self._content_lay = self._make_tab()
        self._build_anki_section()
        self._content_lay.addStretch()
        tabs.addTab(anki_scroll, "Anki")

        # ── Shortcuts tab ──
        sc_scroll, sc_lay = self._make_tab()
        self._build_shortcuts_section(sc_lay)
        tabs.addTab(sc_scroll, "Shortcuts")

        root.addWidget(tabs, stretch=1)

        # Divider + Save/Cancel
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #2a2a2a;")
        root.addWidget(div)

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

    def _build_shortcuts_section(self, lay: QVBoxLayout):
        """Build the shortcuts editor table."""
        hint = QLabel(
            f"Click a shortcut and press your desired key combination.  "
            f"Press Esc to clear.  Uses {'⌘' if __import__('platform').system() == 'Darwin' else 'Ctrl'} "
            f"for modifier keys."
        )
        hint.setStyleSheet("color: #666; font-size: 8pt;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._shortcut_editors: dict[str, QKeySequenceEdit] = {}

        # Group by category
        from collections import defaultdict
        categories = defaultdict(list)
        for action_id, (name, default, cat) in                 self._shortcut_defaults.items():
            categories[cat].append((action_id, name, default))

        for cat_name in ["Navigation", "View", "File", "Bookmarks", "OCR"]:
            if cat_name not in categories:
                continue
            # Category header
            cat_lbl = QLabel(cat_name)
            cat_lbl.setStyleSheet(
                "color: #888; font-size: 8pt; font-weight: bold;"
                " padding-top: 8px;"
            )
            lay.addWidget(cat_lbl)

            for action_id, name, default in categories[cat_name]:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(12)

                name_lbl = QLabel(name)
                name_lbl.setFixedWidth(180)
                name_lbl.setStyleSheet("color: #ccc; font-size: 9pt;")
                row.addWidget(name_lbl)

                editor = QKeySequenceEdit()
                # Load saved or default
                saved = self.app_settings.value(
                    f"shortcuts/{action_id}", default
                )
                if saved:
                    editor.setKeySequence(saved)

                # Wrap in a styled container so we control border/background
                container = QWidget()
                container.setFixedWidth(150)
                container.setFixedHeight(28)
                container.setStyleSheet("""
                    QWidget {
                        background: #2a2a2a;
                        border: 1px solid #555;
                        border-radius: 4px;
                    }
                """)
                c_lay = QHBoxLayout(container)
                c_lay.setContentsMargins(6, 0, 0, 0)
                c_lay.setSpacing(0)

                # Strip all styling from the editor itself
                editor.setStyleSheet("""
                    QKeySequenceEdit {
                        background: transparent;
                        border: none;
                        font-size: 9pt; color: #ddd;
                    }
                    QKeySequenceEdit QLineEdit {
                        background: transparent;
                        border: none;
                        color: #ddd;
                        font-size: 9pt;
                    }
                """)
                c_lay.addWidget(editor)

                # Highlight container border on focus
                def _on_focus(focused, c=container):
                    c.setStyleSheet(
                        "QWidget { background: #252f3d; border: 2px solid #3584e4;"
                        " border-radius: 4px; }"
                        if focused else
                        "QWidget { background: #2a2a2a; border: 1px solid #555;"
                        " border-radius: 4px; }"
                    )
                editor.installEventFilter(self)
                editor.setProperty("container", container)
                editor.setProperty("focus_fn", _on_focus)

                row.addWidget(container)

                reset_btn = QPushButton("Reset")
                reset_btn.setFixedWidth(54)
                reset_btn.setStyleSheet("""
                    QPushButton {
                        background: transparent; color: #666;
                        border: 1px solid #333; border-radius: 4px;
                        padding: 2px 6px; font-size: 8pt;
                    }
                    QPushButton:hover { color: #ccc; border-color: #555; }
                """)
                reset_btn.clicked.connect(
                    lambda _, e=editor, d=default: e.setKeySequence(d)
                )
                row.addWidget(reset_btn)
                row.addStretch()

                self._shortcut_editors[action_id] = editor
                lay.addLayout(row)

        # Reset all button
        lay.addSpacing(12)
        reset_all = QPushButton("Reset All to Defaults")
        reset_all.setStyleSheet("""
            QPushButton {
                background: #2a2a2a; color: #aaa;
                border: 1px solid #444; border-radius: 4px;
                padding: 5px 14px; font-size: 9pt;
            }
            QPushButton:hover { background: #3a3a3a; color: #fff; }
        """)
        def _reset_all():
            for aid, (_, default, _cat) in self._shortcut_defaults.items():
                if aid in self._shortcut_editors:
                    self._shortcut_editors[aid].setKeySequence(default)
        reset_all.clicked.connect(_reset_all)
        lay.addWidget(reset_all)
        lay.addStretch()

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if isinstance(obj, QKeySequenceEdit):
            fn = obj.property("focus_fn")
            if fn:
                if event.type() == QEvent.Type.FocusIn:
                    fn(True)
                elif event.type() == QEvent.Type.FocusOut:
                    fn(False)
        return super().eventFilter(obj, event)

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

        # Preload row: checkbox + spinbox inline
        preload_row_widget = QWidget()
        preload_row_lay = QHBoxLayout(preload_row_widget)
        preload_row_lay.setContentsMargins(0, 0, 0, 0)
        preload_row_lay.setSpacing(8)

        self.preload_check = QCheckBox()
        self.preload_check.setStyleSheet("""
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #444; border-radius: 3px;
                background: #2a2a2a;
            }
            QCheckBox::indicator:checked {
                background: #3584e4; border-color: #3584e4;
            }
        """)
        preload_row_lay.addWidget(self.preload_check)

        self.preload_spin = QSpinBox()
        self.preload_spin.setRange(1, 10)
        self.preload_spin.setValue(2)
        self.preload_spin.setSuffix(" pages")
        self.preload_spin.setFixedWidth(90)
        self.preload_spin.setStyleSheet("""
            QSpinBox {
                background: #2a2a2a; color: #ddd;
                border: 1px solid #444; border-radius: 4px;
                padding: 2px 6px; font-size: 9pt;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px; background: #3a3a3a; border: none;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #3584e4;
            }
        """)
        preload_row_lay.addWidget(self.preload_spin)
        preload_row_lay.addStretch()

        # Spinbox enabled only when checkbox is checked
        self.preload_spin.setEnabled(self.preload_check.isChecked())
        self.preload_check.toggled.connect(self.preload_spin.setEnabled)

        self._row(lay, "Preload Pages", preload_row_widget,
                  hint="Load upcoming pages in the background for instant page turns.")

        self.keep_awake_check = QCheckBox()
        self.keep_awake_check.setStyleSheet("""
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #444; border-radius: 3px; background: #2a2a2a;
            }
            QCheckBox::indicator:checked { background: #3584e4; border-color: #3584e4; }
        """)
        self._row(lay, "Keep Screen Awake", self.keep_awake_check,
                  hint="Prevent the screen from sleeping while a file is open.")

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

        SOURCES = ["— skip —", "Word", "Reading", "Furigana", "Definition", "Sentence", "Image"]
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
        preload_on = self.app_settings.value("general/preload", True, type=bool)
        self.preload_check.setChecked(preload_on)
        self.preload_spin.setEnabled(preload_on)
        self.preload_spin.setValue(self.app_settings.value("general/preload_count", 2, type=int))
        self.keep_awake_check.setChecked(
            self.app_settings.value("general/keep_awake", True, type=bool))

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
        self.app_settings.setValue("general/preload",       self.preload_check.isChecked())
        self.app_settings.setValue("general/preload_count", self.preload_spin.value())
        self.app_settings.setValue("general/keep_awake",    self.keep_awake_check.isChecked())
        self.app_settings.setValue("ocr/device",  self.ocr_device_combo.currentData())
        self.app_settings.setValue("ocr/warmup",       self.ocr_warmup_check.isChecked())
        self.app_settings.setValue("ocr/clear_on_file", self.ocr_clear_on_file_check.isChecked())

        # Anki
        self.app_settings.setValue("anki/url",   self.anki_url.text().strip())
        self.app_settings.setValue("anki/key",   self.anki_key.text().strip())
        self.app_settings.setValue("anki/deck",  self.anki_deck.currentText())
        self.app_settings.setValue("anki/model", self.anki_model.currentText())
        # Shortcuts
        if hasattr(self, "_shortcut_editors"):
            for action_id, editor in self._shortcut_editors.items():
                ks = editor.keySequence().toString()
                default = self._shortcut_defaults.get(action_id, ("","",""))[1]
                if ks == default:
                    # Remove override if same as default
                    self.app_settings.remove(f"shortcuts/{action_id}")
                else:
                    self.app_settings.setValue(f"shortcuts/{action_id}", ks)

        for field, combo in self._field_widgets.items():
            self.app_settings.setValue(f"anki/field/{field}", combo.currentText())

        self.accept()

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog { background: #1a1a1a; color: #e0e0e0; }
            QLabel  { color: #e0e0e0; }
        """)

