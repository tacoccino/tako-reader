"""
Tako Reader — library browser.
Scans a user-configured folder for manga/comic files and lists them
with thumbnail previews in list or grid view.
"""

import re
import os
import hashlib
import zipfile
import tarfile
import shutil
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QFileDialog,
    QWidget, QApplication, QAbstractItemView,
)
from PyQt6.QtCore import (
    Qt, QSettings, QSize, QThread, pyqtSignal, QStandardPaths,
)
from PyQt6.QtGui import QFont, QPixmap, QImage, QIcon

import theme
from utils import load_icon

# ─── Constants ───────────────────────────────────────────────────────────────

SUPPORTED_EXTS = {
    ".cbz", ".cbr", ".cb7", ".cbt",
    ".zip", ".rar", ".7z", ".tar",
    ".pdf",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}

THUMB_W, THUMB_H = 120, 160
LIST_ICON_W, LIST_ICON_H = 50, 70


def _nat_key(s: str):
    """Natural sort key."""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', s)]


def _is_image_name(name: str) -> bool:
    return (Path(name).suffix.lower() in IMAGE_EXTS
            and not name.startswith("__")
            and not name.startswith("._"))


# ─── Thumbnail cache ─────────────────────────────────────────────────────────

def _thumb_cache_dir() -> Path:
    cache = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    d = Path(cache) / "TakoReader" / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _thumb_key(file_path: str) -> str:
    """Cache key: MD5 of path + mtime."""
    try:
        mtime = str(os.path.getmtime(file_path))
    except Exception:
        mtime = "0"
    raw = f"{file_path}|{mtime}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cached_thumb_path(file_path: str) -> Path:
    return _thumb_cache_dir() / f"{_thumb_key(file_path)}.jpg"


def get_cache_size_bytes() -> int:
    """Return total size of cached thumbnails in bytes."""
    d = _thumb_cache_dir()
    if not d.exists():
        return 0
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


def clear_cache():
    """Delete all cached thumbnails."""
    d = _thumb_cache_dir()
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


# ─── First-page extraction ───────────────────────────────────────────────────

def _extract_first_page(file_path: str) -> QPixmap | None:
    """Extract the first page from a manga file and return as QPixmap."""
    p = Path(file_path)
    ext = p.suffix.lower()

    try:
        if ext in (".cbz", ".zip"):
            return _first_page_cbz(file_path)
        elif ext in (".cbr", ".rar"):
            return _first_page_cbr(file_path)
        elif ext in (".cb7", ".7z"):
            return _first_page_cb7(file_path)
        elif ext in (".cbt", ".tar"):
            return _first_page_cbt(file_path)
        elif ext == ".pdf":
            return _first_page_pdf(file_path)
        elif ext in IMAGE_EXTS:
            px = QPixmap(file_path)
            return px if not px.isNull() else None
        elif p.is_dir():
            return _first_page_dir(file_path)
    except Exception:
        pass
    return None


def _first_page_cbz(path: str) -> QPixmap | None:
    with zipfile.ZipFile(path, "r") as zf:
        names = sorted((n for n in zf.namelist() if _is_image_name(n)),
                        key=_nat_key)
        if not names:
            return None
        img = QImage()
        img.loadFromData(zf.read(names[0]))
        return QPixmap.fromImage(img) if not img.isNull() else None


def _first_page_cbr(path: str) -> QPixmap | None:
    import subprocess, tempfile, platform
    tool = None
    for name in ("unrar", "unar"):
        if shutil.which(name):
            tool = name
            break
    if not tool:
        return None
    with tempfile.TemporaryDirectory(prefix="tako_thumb_") as tmpdir:
        try:
            kwargs = {}
            if platform.system() == "Windows":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            if tool == "unrar":
                subprocess.run(
                    ["unrar", "x", "-o+", "-inul", path, tmpdir + "/"],
                    check=True, capture_output=True, **kwargs,
                )
            else:
                subprocess.run(
                    ["unar", "-force-overwrite", "-no-directory",
                     "-output-directory", tmpdir, path],
                    check=True, capture_output=True, **kwargs,
                )
        except Exception:
            return None
        files = sorted(
            (f for f in Path(tmpdir).rglob("*")
             if f.is_file() and f.suffix.lower() in IMAGE_EXTS
             and not f.name.startswith("._")),
            key=lambda x: _nat_key(str(x))
        )
        if not files:
            return None
        px = QPixmap(str(files[0]))
        return px if not px.isNull() else None


def _first_page_cb7(path: str) -> QPixmap | None:
    try:
        import py7zr
    except ImportError:
        return None
    with py7zr.SevenZipFile(path, "r") as zf:
        all_files = zf.readall()
        names = sorted((n for n in all_files.keys() if _is_image_name(n)),
                        key=_nat_key)
        if not names:
            return None
        data = all_files[names[0]].read()
        img = QImage()
        img.loadFromData(data)
        return QPixmap.fromImage(img) if not img.isNull() else None


def _first_page_cbt(path: str) -> QPixmap | None:
    with tarfile.open(path, "r:*") as tf:
        members = sorted(
            [m for m in tf.getmembers()
             if m.isfile() and _is_image_name(m.name)],
            key=lambda m: _nat_key(m.name)
        )
        if not members:
            return None
        f = tf.extractfile(members[0])
        if f is None:
            return None
        data = f.read()
        img = QImage()
        img.loadFromData(data)
        return QPixmap.fromImage(img) if not img.isNull() else None


def _first_page_pdf(path: str) -> QPixmap | None:
    try:
        import fitz
    except ImportError:
        return None
    doc = fitz.open(path)
    if len(doc) == 0:
        doc.close()
        return None
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
    img = QImage(pix.samples, pix.width, pix.height,
                 pix.stride, QImage.Format.Format_RGB888)
    px = QPixmap.fromImage(img.copy())
    doc.close()
    return px if not px.isNull() else None


def _first_page_dir(path: str) -> QPixmap | None:
    files = sorted(
        (f for f in Path(path).iterdir()
         if f.suffix.lower() in IMAGE_EXTS),
        key=lambda x: _nat_key(x.name)
    )
    if not files:
        return None
    px = QPixmap(str(files[0]))
    return px if not px.isNull() else None


# ─── Library scanner ─────────────────────────────────────────────────────────

def _scan_library(root: Path) -> list[dict]:
    """Scan a directory tree for supported manga/comic files."""
    results = []
    if not root.is_dir():
        return results

    for p in sorted(root.rglob("*"), key=lambda x: _nat_key(str(x))):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            rel_parent = p.parent.relative_to(root) if p.parent != root else Path("")
            results.append({
                "name": p.name,
                "path": str(p),
                "folder": str(rel_parent) if str(rel_parent) != "." else "",
                "ext": p.suffix.lower(),
            })

    # Image directories
    for d in sorted(root.rglob("*"), key=lambda x: _nat_key(str(x))):
        if not d.is_dir():
            continue
        has_archives = any(
            f.suffix.lower() in SUPPORTED_EXTS
            for f in d.iterdir() if f.is_file()
        )
        if has_archives:
            continue
        has_images = any(
            f.suffix.lower() in IMAGE_EXTS
            for f in d.iterdir() if f.is_file()
        )
        if has_images:
            rel_parent = d.parent.relative_to(root) if d.parent != root else Path("")
            results.append({
                "name": d.name,
                "path": str(d),
                "folder": str(rel_parent) if str(rel_parent) != "." else "",
                "ext": "(folder)",
            })

    return results


# ─── Thumbnail worker ────────────────────────────────────────────────────────

class ThumbnailWorker(QThread):
    """Generate thumbnails in the background, emitting each as it's ready."""
    thumbnail_ready = pyqtSignal(str, QPixmap)  # (file_path, thumb_pixmap)
    finished_all    = pyqtSignal()

    def __init__(self, entries: list[dict]):
        super().__init__()
        self._entries = entries
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for entry in self._entries:
            if self._stop:
                break
            file_path = entry["path"]
            cached = _cached_thumb_path(file_path)

            # Check cache
            if cached.exists():
                px = QPixmap(str(cached))
                if not px.isNull():
                    self.thumbnail_ready.emit(file_path, px)
                    continue

            # Generate
            full_px = _extract_first_page(file_path)
            if full_px and not full_px.isNull():
                thumb = full_px.scaled(
                    THUMB_W, THUMB_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                # Save to cache
                try:
                    thumb.save(str(cached), "JPEG", 85)
                except Exception:
                    pass
                self.thumbnail_ready.emit(file_path, thumb)

        self.finished_all.emit()


# ─── Library dialog ──────────────────────────────────────────────────────────

class LibraryDialog(QDialog):
    """
    Library browser dialog. Lists all manga/comic files found in the
    user's configured library folder with thumbnail previews.
    """

    def __init__(self, app_settings: QSettings, open_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library — Tako Reader")
        self.resize(750, 550)
        self.setMinimumSize(400, 300)
        self.app_settings = app_settings
        self._open_callback = open_callback
        self._entries: list[dict] = []
        self._thumb_worker: ThumbnailWorker | None = None
        self._view_mode = app_settings.value("library/view_mode", "list")
        self._item_map: dict[str, QListWidgetItem] = {}  # path -> item

        self.setStyleSheet(
            f"QDialog {{ background: {theme._active['window_bg']};"
            f" color: {theme._active['text']}; }}"
        )

        self._build_ui()
        self._load_library()

    def _build_ui(self):
        icon_btn_style = f"""
            QPushButton {{
                background: transparent; border: 1px solid {theme._active['border']};
                border-radius: 6px; padding: 4px;
            }}
            QPushButton:hover {{ background: {theme._active['hover_bg']}; }}
        """

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

        self._refresh_btn = QPushButton()
        self._refresh_btn.setFixedSize(30, 30)
        self._refresh_btn.setStyleSheet(icon_btn_style)
        self._refresh_btn.setToolTip("Refresh")
        ic_refresh = load_icon("refresh")
        if not ic_refresh.isNull():
            self._refresh_btn.setIcon(ic_refresh)
            self._refresh_btn.setIconSize(QSize(16, 16))
        else:
            self._refresh_btn.setText("↻")
        self._refresh_btn.clicked.connect(self._load_library)
        header.addWidget(self._refresh_btn)

        root.addLayout(header)

        # ── Search bar + view toggle ──
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

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
        search_row.addWidget(self._search, stretch=1)

        self._view_btn = QPushButton()
        self._view_btn.setFixedSize(30, 30)
        self._view_btn.setStyleSheet(icon_btn_style)
        self._view_btn.setToolTip("Toggle list / grid view")
        self._view_btn.clicked.connect(self._toggle_view)
        self._update_view_btn_icon()
        search_row.addWidget(self._view_btn)

        root.addLayout(search_row)

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
            f" padding: 4px; border-radius: 4px;"
            f"}}"
            f" QListWidget::item:selected {{"
            f" background: {theme.ACCENT}; color: #fff;"
            f"}}"
            f" QListWidget::item:hover {{"
            f" background: {theme._active['hover_bg']};"
            f"}}"
        )
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._apply_view_mode()
        root.addWidget(self._list, stretch=1)

        # ── Status ──
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {theme._active['text_muted']}; font-size: 9pt;"
        )
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status)

        # ── Set folder button (shown only when no library is configured) ──
        self._set_folder_btn = QPushButton("Set Library Folder…")
        self._set_folder_btn.setStyleSheet(theme.BTN_MAIN)
        self._set_folder_btn.clicked.connect(self._pick_folder)
        self._set_folder_btn.setVisible(False)
        root.addWidget(self._set_folder_btn, alignment=Qt.AlignmentFlag.AlignCenter)

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

    # ── View mode ────────────────────────────────────────────────────────────

    def _toggle_view(self):
        if self._view_mode == "list":
            self._view_mode = "grid"
        else:
            self._view_mode = "list"
        self.app_settings.setValue("library/view_mode", self._view_mode)
        self._update_view_btn_icon()
        self._apply_view_mode()
        self._filter_list(self._search.text())

    def _update_view_btn_icon(self):
        icon_name = "view-list" if self._view_mode == "grid" else "view-grid"
        ic = load_icon(icon_name)
        if not ic.isNull():
            self._view_btn.setIcon(ic)
            self._view_btn.setIconSize(QSize(16, 16))
            self._view_btn.setText("")
        else:
            self._view_btn.setText("▦" if self._view_mode == "list" else "≡")

    def _apply_view_mode(self):
        if self._view_mode == "grid":
            self._list.setViewMode(QListWidget.ViewMode.IconMode)
            self._list.setIconSize(QSize(THUMB_W, THUMB_H))
            self._list.setGridSize(QSize(THUMB_W + 16, THUMB_H + 24))
            self._list.setFlow(QListWidget.Flow.LeftToRight)
            self._list.setWrapping(True)
            self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
            self._list.setMovement(QListWidget.Movement.Static)
        else:
            self._list.setViewMode(QListWidget.ViewMode.ListMode)
            self._list.setIconSize(QSize(LIST_ICON_W, LIST_ICON_H))
            self._list.setGridSize(QSize(-1, -1))
            self._list.setFlow(QListWidget.Flow.TopToBottom)
            self._list.setWrapping(False)

    # ── Library loading ──────────────────────────────────────────────────────

    def _load_library(self):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(500)

        lib_path = self.app_settings.value("library/path", "")
        self._list.clear()
        self._entries = []
        self._item_map = {}

        if not lib_path or not Path(lib_path).is_dir():
            self._status.setText("No library folder set.")
            self._search.setEnabled(False)
            self._set_folder_btn.setVisible(True)
            return

        self._set_folder_btn.setVisible(False)
        self._search.setEnabled(True)
        self._status.setText("Scanning…")
        QApplication.processEvents()

        self._entries = _scan_library(Path(lib_path))
        self._populate_list(self._entries)

        count = len(self._entries)
        self._status.setText(
            f"{count} item{'s' if count != 1 else ''} found"
            if count else "No manga or comic files found in this folder."
        )

        if self._entries:
            self._thumb_worker = ThumbnailWorker(self._entries)
            self._thumb_worker.thumbnail_ready.connect(self._on_thumb_ready)
            self._thumb_worker.start()

    def _populate_list(self, entries: list[dict]):
        self._list.clear()
        self._item_map = {}

        placeholder = QPixmap(THUMB_W, THUMB_H)
        placeholder.fill(Qt.GlobalColor.transparent)

        for entry in entries:
            if self._view_mode == "grid":
                display = ""
            else:
                if entry["folder"]:
                    display = f"{entry['name']}    —  {entry['folder']}"
                else:
                    display = entry["name"]

            item = QListWidgetItem(QIcon(placeholder), display)
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            item.setToolTip(
                entry["name"] if self._view_mode == "grid"
                else entry["path"]
            )
            self._list.addItem(item)
            self._item_map[entry["path"]] = item

    def _on_thumb_ready(self, file_path: str, thumb: QPixmap):
        item = self._item_map.get(file_path)
        if item:
            item.setIcon(QIcon(thumb))

    # ── Search filter ────────────────────────────────────────────────────────

    def _filter_list(self, text: str):
        text = text.strip().lower()
        if not text:
            self._populate_list(self._entries)
        else:
            filtered = [
                e for e in self._entries
                if text in e["name"].lower() or text in e["folder"].lower()
            ]
            self._populate_list(filtered)
            self._status.setText(
                f"{len(filtered)} of {len(self._entries)} items"
            )
        self._apply_cached_thumbs()

    def _apply_cached_thumbs(self):
        for path, item in self._item_map.items():
            cached = _cached_thumb_path(path)
            if cached.exists():
                px = QPixmap(str(cached))
                if not px.isNull():
                    item.setIcon(QIcon(px))

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

    def closeEvent(self, event):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(1000)
        super().closeEvent(event)
