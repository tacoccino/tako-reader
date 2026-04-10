"""
Tako Reader — library browser.
Scans a user-configured folder for manga/comic files and lists them
with thumbnail previews, metadata-aware display, grouping, and filtering.
"""

import re
import os
import hashlib
import json as _json
import zipfile
import tarfile
import shutil
from pathlib import Path
from collections import defaultdict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QFileDialog,
    QWidget, QApplication, QComboBox,
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

RATING_FILTERS = ["Show All", "Hide NSFW", "SFW Only", "NSFW Only"]


def _nat_key(s: str):
    """Natural sort key."""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', s)]


def _is_image_name(name: str) -> bool:
    return (Path(name).suffix.lower() in IMAGE_EXTS
            and not name.startswith("__")
            and not name.startswith("._"))


# ─── Metadata helpers ─────────────────────────────────────────────────────────

def _meta_key_for_path(file_path: str) -> str:
    h = hashlib.md5(file_path.encode()).hexdigest()[:12]
    return f"metadata/{h}"


def _load_meta(settings: QSettings, file_path: str) -> dict:
    raw = settings.value(_meta_key_for_path(file_path), "{}")
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def _display_title(entry: dict, meta: dict) -> str:
    """Build a display title from metadata, falling back to filename."""
    title = meta.get("title_jp") or meta.get("title_en") or ""
    vol = meta.get("volume", "")
    if title:
        if vol:
            return f"{title} — Vol {vol}"
        return title
    return entry["name"]


# ─── Thumbnail cache ─────────────────────────────────────────────────────────

def _thumb_cache_dir() -> Path:
    cache = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    d = Path(cache) / "TakoReader" / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _thumb_key(file_path: str) -> str:
    try:
        mtime = str(os.path.getmtime(file_path))
    except Exception:
        mtime = "0"
    raw = f"{file_path}|{mtime}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cached_thumb_path(file_path: str) -> Path:
    return _thumb_cache_dir() / f"{_thumb_key(file_path)}.jpg"


def get_cache_size_bytes() -> int:
    d = _thumb_cache_dir()
    if not d.exists():
        return 0
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


def clear_cache():
    d = _thumb_cache_dir()
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


# ─── First-page extraction ───────────────────────────────────────────────────

def _extract_first_page(file_path: str) -> QPixmap | None:
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
    thumbnail_ready = pyqtSignal(str, QPixmap)
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

            if cached.exists():
                px = QPixmap(str(cached))
                if not px.isNull():
                    self.thumbnail_ready.emit(file_path, px)
                    continue

            full_px = _extract_first_page(file_path)
            if full_px and not full_px.isNull():
                thumb = full_px.scaled(
                    THUMB_W, THUMB_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                try:
                    thumb.save(str(cached), "JPEG", 85)
                except Exception:
                    pass
                self.thumbnail_ready.emit(file_path, thumb)

        self.finished_all.emit()


# ─── Library dialog ──────────────────────────────────────────────────────────

class LibraryDialog(QDialog):

    def __init__(self, app_settings: QSettings, open_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Library — Tako Reader")
        self.resize(750, 550)
        self.setMinimumSize(400, 300)
        self.app_settings = app_settings
        self._open_callback = open_callback
        self._entries: list[dict] = []
        self._meta_cache: dict[str, dict] = {}  # path -> metadata dict
        self._thumb_worker: ThumbnailWorker | None = None
        self._view_mode = app_settings.value("library/view_mode", "list")
        self._rating_filter_idx = 0  # index into RATING_FILTERS
        self._item_map: dict[str, QListWidgetItem] = {}
        self._group_items: list[QListWidgetItem] = []  # group header items

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

        # Rating filter
        self._rating_btn = QPushButton("Show All")
        self._rating_btn.setStyleSheet(theme.BTN_MAIN)
        self._rating_btn.setFixedWidth(90)
        self._rating_btn.clicked.connect(self._cycle_rating_filter)
        header.addWidget(self._rating_btn)

        # Sort
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Title", "Filename", "Author", "Year"])
        self._sort_combo.setStyleSheet(
            f"QComboBox {{"
            f" background: {theme._active['input_bg']}; color: {theme._active['text']};"
            f" border: 1px solid {theme._active['border']}; border-radius: 6px;"
            f" padding: 4px 8px; font-size: 9pt;"
            f"}}"
        )
        self._sort_combo.setFixedWidth(90)
        self._sort_combo.currentIndexChanged.connect(lambda: self._refresh_display())
        header.addWidget(self._sort_combo)

        # Refresh
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
        self._search.textChanged.connect(lambda: self._refresh_display())
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
        self._view_mode = "grid" if self._view_mode == "list" else "list"
        self.app_settings.setValue("library/view_mode", self._view_mode)
        self._update_view_btn_icon()
        self._apply_view_mode()
        self._refresh_display()

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

    # ── Rating filter ────────────────────────────────────────────────────────

    def _cycle_rating_filter(self):
        self._rating_filter_idx = (self._rating_filter_idx + 1) % len(RATING_FILTERS)
        self._rating_btn.setText(RATING_FILTERS[self._rating_filter_idx])
        self._refresh_display()

    def _passes_rating_filter(self, meta: dict) -> bool:
        mode = RATING_FILTERS[self._rating_filter_idx]
        rating = meta.get("rating", "")
        if mode == "Show All":
            return True
        elif mode == "Hide NSFW":
            return rating != "NSFW"
        elif mode == "SFW Only":
            return rating == "SFW" or rating == ""
        elif mode == "NSFW Only":
            return rating == "NSFW"
        return True

    # ── Library loading ──────────────────────────────────────────────────────

    def _load_library(self):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.stop()
            self._thumb_worker.wait(500)

        lib_path = self.app_settings.value("library/path", "")
        self._list.clear()
        self._entries = []
        self._meta_cache = {}
        self._item_map = {}
        self._group_items = []

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

        # Pre-load all metadata
        for entry in self._entries:
            self._meta_cache[entry["path"]] = _load_meta(
                self.app_settings, entry["path"]
            )

        self._refresh_display()

        # Start thumbnail generation
        if self._entries:
            self._thumb_worker = ThumbnailWorker(self._entries)
            self._thumb_worker.thumbnail_ready.connect(self._on_thumb_ready)
            self._thumb_worker.start()

    # ── Display refresh ──────────────────────────────────────────────────────

    def _refresh_display(self):
        """Apply search, rating filter, sort, grouping, and repopulate."""
        search = self._search.text().strip().lower()

        # Filter
        filtered = []
        for entry in self._entries:
            meta = self._meta_cache.get(entry["path"], {})

            # Rating filter
            if not self._passes_rating_filter(meta):
                continue

            # Search filter — match against name, folder, and all metadata
            if search:
                searchable = " ".join([
                    entry["name"].lower(),
                    entry["folder"].lower(),
                    meta.get("title_jp", "").lower(),
                    meta.get("title_en", "").lower(),
                    meta.get("author", "").lower(),
                    meta.get("artist", "").lower(),
                    meta.get("circle", "").lower(),
                    meta.get("publisher", "").lower(),
                    meta.get("tags", "").lower(),
                    meta.get("year", "").lower(),
                ])
                if search not in searchable:
                    continue

            filtered.append(entry)

        # Sort
        sort_mode = self._sort_combo.currentText()
        filtered = self._sort_entries(filtered, sort_mode)

        # Populate
        if self._view_mode == "grid":
            self._populate_flat(filtered)
        else:
            self._populate_grouped(filtered)

        self._apply_cached_thumbs()

        # Status
        total = len(self._entries)
        shown = len(filtered)
        if shown == total:
            self._status.setText(
                f"{total} item{'s' if total != 1 else ''}"
                if total else "No manga or comic files found."
            )
        else:
            self._status.setText(f"{shown} of {total} items")

    def _sort_entries(self, entries: list[dict], mode: str) -> list[dict]:
        def sort_key(e):
            meta = self._meta_cache.get(e["path"], {})
            if mode == "Title":
                title = _display_title(e, meta)
                return _nat_key(title)
            elif mode == "Author":
                return _nat_key(meta.get("author", "") or e["name"])
            elif mode == "Year":
                return meta.get("year", "9999")
            else:  # Filename
                return _nat_key(e["name"])
        return sorted(entries, key=sort_key)

    def _populate_grouped(self, entries: list[dict]):
        """List view: group entries by title, with group headers."""
        self._list.clear()
        self._item_map = {}
        self._group_items = []

        placeholder = QPixmap(LIST_ICON_W, LIST_ICON_H)
        placeholder.fill(Qt.GlobalColor.transparent)

        # Group by title (entries with same title_jp or title_en)
        groups: dict[str, list[dict]] = defaultdict(list)
        ungrouped: list[dict] = []

        for entry in entries:
            meta = self._meta_cache.get(entry["path"], {})
            group_key = meta.get("title_jp") or meta.get("title_en") or ""
            if group_key:
                groups[group_key].append(entry)
            else:
                ungrouped.append(entry)

        # Sort groups by title
        sorted_groups = sorted(groups.items(), key=lambda kv: _nat_key(kv[0]))

        # Add grouped entries
        for group_title, group_entries in sorted_groups:
            if len(group_entries) > 1:
                # Add group header
                header_item = QListWidgetItem(f"  {group_title}  ({len(group_entries)})")
                header_item.setFlags(Qt.ItemFlag.NoItemFlags)
                header_item.setData(Qt.ItemDataRole.UserRole, None)
                header_item.setFont(QFont("", 10, QFont.Weight.Bold))
                header_item.setBackground(
                    Qt.GlobalColor.transparent
                )
                header_item.setForeground(
                    QLabel().palette().color(QLabel().foregroundRole())
                )
                # Use a subtle style for group headers
                header_item.setSizeHint(QSize(-1, 28))
                self._list.addItem(header_item)
                self._group_items.append(header_item)

            for entry in group_entries:
                meta = self._meta_cache.get(entry["path"], {})
                display = _display_title(entry, meta)
                if entry["folder"]:
                    display += f"    —  {entry['folder']}"
                item = QListWidgetItem(QIcon(placeholder), display)
                item.setData(Qt.ItemDataRole.UserRole, entry["path"])
                item.setToolTip(entry["path"])
                self._list.addItem(item)
                self._item_map[entry["path"]] = item

        # Add ungrouped entries
        if ungrouped and sorted_groups:
            sep_item = QListWidgetItem("  Ungrouped")
            sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
            sep_item.setData(Qt.ItemDataRole.UserRole, None)
            sep_item.setFont(QFont("", 10, QFont.Weight.Bold))
            sep_item.setSizeHint(QSize(-1, 28))
            self._list.addItem(sep_item)
            self._group_items.append(sep_item)

        for entry in ungrouped:
            meta = self._meta_cache.get(entry["path"], {})
            display = _display_title(entry, meta)
            if entry["folder"]:
                display += f"    —  {entry['folder']}"
            item = QListWidgetItem(QIcon(placeholder), display)
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            item.setToolTip(entry["path"])
            self._list.addItem(item)
            self._item_map[entry["path"]] = item

    def _populate_flat(self, entries: list[dict]):
        """Grid view: flat list, no grouping."""
        self._list.clear()
        self._item_map = {}
        self._group_items = []

        placeholder = QPixmap(THUMB_W, THUMB_H)
        placeholder.fill(Qt.GlobalColor.transparent)

        for entry in entries:
            meta = self._meta_cache.get(entry["path"], {})
            display_name = _display_title(entry, meta)
            item = QListWidgetItem(QIcon(placeholder), "")
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            item.setToolTip(display_name)
            self._list.addItem(item)
            self._item_map[entry["path"]] = item

    def _on_thumb_ready(self, file_path: str, thumb: QPixmap):
        item = self._item_map.get(file_path)
        if item:
            item.setIcon(QIcon(thumb))

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
