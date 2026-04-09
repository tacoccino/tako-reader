"""
Tako Reader — library browser.
Scans a user-configured folder for manga/comic files and lists them
in a searchable dialog.
"""

import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QFileDialog,
    QWidget,
)
from PyQt6.QtCore import Qt, QSettings, QSize
from PyQt6.QtGui import QFont

import theme

# Supported archive/document extensions (same as loaders.py)
SUPPORTED_EXTS = {
    ".cbz", ".cbr", ".cb7", ".cbt",
    ".zip", ".rar", ".7z", ".tar",
    ".pdf",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}


def _nat_key(s: str):
    """Natural sort key."""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', s)]


def _scan_library(root: Path) -> list[dict]:
    """Scan a directory tree for supported manga/comic files.
    Returns a list of {'name': str, 'path': str, 'folder': str, 'ext': str}.
    Also detects directories that contain images (treated as a manga folder).
    """
    results = []
    if not root.is_dir():
        return results

    for p in sorted(root.rglob("*"), key=lambda x: _nat_key(str(x))):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            # Use parent relative to library root for context
            rel_parent = p.parent.relative_to(root) if p.parent != root else Path("")
            results.append({
                "name": p.name,
                "path": str(p),
                "folder": str(rel_parent) if str(rel_parent) != "." else "",
                "ext": p.suffix.lower(),
            })

    # Also detect image directories (folders containing images but no archives)
    for d in sorted(root.rglob("*"), key=lambda x: _nat_key(str(x))):
        if not d.is_dir():
            continue
        # Skip if this directory has archive files (already listed)
        has_archives = any(
            f.suffix.lower() in SUPPORTED_EXTS
            for f in d.iterdir() if f.is_file()
        )
        if has_archives:
            continue
        # Check if it has image files
        has_images = any(
            f.suffix.lower() in IMAGE_EXTS
            for f in d.iterdir() if f.is_file()
        )
        if has_images:
            rel_parent = d.parent.relative_to(root) if d.parent != root else Path("")
            results.append({
                "name": f"📁 {d.name}",
                "path": str(d),
                "folder": str(rel_parent) if str(rel_parent) != "." else "",
                "ext": "(folder)",
            })

    return results


class LibraryDialog(QDialog):
    """
    Library browser dialog. Lists all manga/comic files found in the
    user's configured library folder.
    """

    def __init__(self, app_settings: QSettings, open_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library — Tako Reader")
        self.resize(600, 500)
        self.setMinimumSize(400, 300)
        self.app_settings = app_settings
        self._open_callback = open_callback
        self._entries: list[dict] = []

        self.setStyleSheet(
            f"QDialog {{ background: {theme._active['window_bg']};"
            f" color: {theme._active['text']}; }}"
        )

        self._build_ui()
        self._load_library()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ── Header ──
        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("Library")
        title.setFont(QFont("", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {theme._active['text']};")
        header.addWidget(title, stretch=1)

        self._change_btn = QPushButton("Change Folder…")
        self._change_btn.setStyleSheet(theme.BTN_MAIN)
        self._change_btn.clicked.connect(self._pick_folder)
        header.addWidget(self._change_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(theme.BTN_MAIN)
        refresh_btn.clicked.connect(self._load_library)
        header.addWidget(refresh_btn)

        root.addLayout(header)

        # ── Library path label ──
        self._path_label = QLabel("")
        self._path_label.setStyleSheet(
            f"color: {theme._active['text_muted']}; font-size: 8pt;"
        )
        self._path_label.setWordWrap(True)
        root.addWidget(self._path_label)

        # ── Search bar ──
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            f"QLineEdit {{"
            f" background: {theme._active['input_bg']};"
            f" color: {theme._active['text']};"
            f" border: 1px solid {theme._active['border_light']};"
            f" border-radius: 6px; padding: 6px 10px; font-size: 10pt;"
            f"}}"
        )
        self._search.textChanged.connect(self._filter_list)
        root.addWidget(self._search)

        # ── File list ──
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{"
            f" background: {theme._active['card_bg']};"
            f" border: 1px solid {theme._active['border_light']};"
            f" border-radius: 6px; padding: 4px;"
            f"}}"
            f" QListWidget::item {{"
            f" color: {theme._active['text']};"
            f" padding: 6px 8px; border-radius: 4px;"
            f"}}"
            f" QListWidget::item:selected {{"
            f" background: {theme.ACCENT}; color: #fff;"
            f"}}"
            f" QListWidget::item:hover {{"
            f" background: {theme._active['hover_bg']};"
            f"}}"
        )
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        root.addWidget(self._list, stretch=1)

        # ── Status / empty state ──
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {theme._active['text_muted']}; font-size: 9pt;"
        )
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status)

        # ── Bottom buttons ──
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch()

        open_btn = QPushButton("Open")
        open_btn.setStyleSheet(theme.BTN_MAIN)
        open_btn.clicked.connect(self._open_selected)
        bottom.addWidget(open_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.BTN_MAIN)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        root.addLayout(bottom)

    # ── Library loading ──────────────────────────────────────────────────────

    def _load_library(self):
        lib_path = self.app_settings.value("library/path", "")
        self._list.clear()
        self._entries = []

        if not lib_path or not Path(lib_path).is_dir():
            self._path_label.setText("No library folder set.")
            self._status.setText("Set a library folder to get started.")
            self._search.setEnabled(False)
            return

        self._path_label.setText(lib_path)
        self._search.setEnabled(True)
        self._status.setText("Scanning…")

        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        self._entries = _scan_library(Path(lib_path))
        self._populate_list(self._entries)

        count = len(self._entries)
        self._status.setText(
            f"{count} item{'s' if count != 1 else ''} found"
            if count else "No manga or comic files found in this folder."
        )

    def _populate_list(self, entries: list[dict]):
        self._list.clear()
        for entry in entries:
            if entry["folder"]:
                display = f"{entry['name']}    —  {entry['folder']}"
            else:
                display = entry["name"]
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            item.setToolTip(entry["path"])
            self._list.addItem(item)

    # ── Search filter ────────────────────────────────────────────────────────

    def _filter_list(self, text: str):
        text = text.strip().lower()
        if not text:
            self._populate_list(self._entries)
            return
        filtered = [
            e for e in self._entries
            if text in e["name"].lower() or text in e["folder"].lower()
        ]
        self._populate_list(filtered)
        self._status.setText(
            f"{len(filtered)} of {len(self._entries)} items"
        )

    # ── Actions ──────────────────────────────────────────────────────────────

    def _pick_folder(self):
        current = self.app_settings.value("library/path", "")
        path = QFileDialog.getExistingDirectory(
            self, "Choose Library Folder", current
        )
        if path:
            self.app_settings.setValue("library/path", path)
            self._load_library()

    def _open_selected(self):
        item = self._list.currentItem()
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                self._open_callback(path)
                self.accept()

    def _on_item_double_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._open_callback(path)
            self.accept()
