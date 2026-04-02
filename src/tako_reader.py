#!/usr/bin/env python3
"""
Tako Reader (タコReader) - Japanese Learning Edition
Supports CBZ, PDF, and image files with Japanese OCR
"""

import sys
import json
import subprocess
import platform
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QScrollArea,
    QDialog, QFrame, QMessageBox,
    QProgressDialog, QLineEdit,
)
from PyQt6.QtCore import (
    Qt, QSize, QRect, QPoint,
    QSettings, QTimer,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QAction,
    QIcon, QPainter, QTransform,
)

# ─── Module imports ──────────────────────────────────────────────────────────

from utils import load_icon, _ctrl, dlog, DEBUG, is_frozen
import theme
from ocr import OCRProcessManager, OCRWorker, OCRWarmupWorker, shutdown_ocr, _InProcessModel
from loaders import load_pages_from_path
from series import SeriesContext
from widgets import (
    PageView, OCRPanel,
    PagePreloadWorker, BookmarkPopup, ImageAdjustPopup,
    MarqueeOverlay, ThumbnailList,
)
from settings import SettingsDialog


# ─── Main Window ─────────────────────────────────────────────────────────────

class TakoReader(QMainWindow):

    # (display_name, default_shortcut, category)
    # Empty string = no default shortcut
    SHORTCUT_DEFAULTS: dict[str, tuple[str, str, str]] = {
        # Navigation
        "next_page":        ("Next Page",            "Right",        "Navigation"),
        "prev_page":        ("Previous Page",         "Left",         "Navigation"),
        "first_page":       ("First Page",            "Home",         "Navigation"),
        "last_page":        ("Last Page",             "End",          "Navigation"),
        "jump_to_page":     ("Jump to Page",          "Ctrl+G",       "Navigation"),
        "prev_volume":      ("Previous Volume",       "Ctrl+Left",    "Navigation"),
        "next_volume":      ("Next Volume",           "Ctrl+Right",   "Navigation"),
        # View
        "fit_width":        ("Fit Width",             "W",            "View"),
        "fit_page":         ("Fit Page",              "F",            "View"),
        "zoom_in":          ("Zoom In",               "Ctrl+=",       "View"),
        "zoom_out":         ("Zoom Out",              "Ctrl+-",       "View"),
        "fullscreen":       ("Toggle Fullscreen",     "F11",          "View"),
        "rotate_left":      ("Rotate Left",           "[",            "View"),
        "rotate_right":     ("Rotate Right",          "]",            "View"),
        "reset_rotation":   ("Reset Rotation",        "",             "View"),
        "single_page":      ("Single Page",           "",             "View"),
        "double_page":      ("Double Page",           "",             "View"),
        "page_offset":      ("Toggle Page Offset",    "Shift+O",      "View"),
        "toggle_warmth":    ("Toggle Warmth",         "",             "View"),
        "toggle_thumbnails":("Toggle Thumbnails",     "Ctrl+Shift+T", "View"),
        "toggle_ocr_panel": ("Toggle OCR Panel",      "Ctrl+Shift+P", "View"),
        # File
        "open_file":        ("Open File",             "Ctrl+O",       "File"),
        "close_file":       ("Close File",            "Ctrl+W",       "File"),
        # Bookmarks
        "toggle_bookmark":  ("Toggle Bookmark",       "Ctrl+B",       "Bookmarks"),
        "show_bookmarks":   ("Show Bookmarks",        "Ctrl+Shift+B", "Bookmarks"),
        # OCR
        "ocr_mode":         ("OCR Selection Mode",    "Ctrl+Shift+O", "OCR"),
        "dict_lookup":      ("Dictionary Lookup",     "Ctrl+D",       "OCR"),
    }

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
        self._actions: dict                = {}  # action_id → QAction
        self._current_file                 = ""
        self._page_mode                    = "single"  # "single" | "double"
        self._page_offset                  = 0          # 0 or 1; 1 = first page solo
        self._spreads: list[tuple[int,...]] = []         # precomputed page pairings
        self._rotation                     = 0          # 0, 90, 180, 270
        self._adjustments                  = {"brightness": 100, "contrast": 100,
                                              "saturation": 100, "sharpness": 100,
                                              "warmth": 0}
        self._adj_cache: dict               = {}   # (index, adj_key) → QPixmap
        self._adj_debounce                  = None  # QTimer, set up after build
        self._series: SeriesContext | None   = None
        self._at_volume_boundary            = False  # for two-press advance

        # Initialise theme engine before building UI so load_icon() uses
        # the correct icon variant (dark/light) from the very first call.
        _tid    = self._settings.value("ui/theme",  "dark")
        _accent = self._settings.value("ui/accent", theme.DEFAULT_ACCENT)
        theme.apply_theme(_tid, _accent)

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._apply_theme()   # applies the stylesheet to the window
        self._restore_settings()
        self.setAcceptDrops(True)
        # Keep-awake and auto-hide cursor state — must be before installEventFilter
        self._keep_awake_active = False
        self._cursor_hidden     = False
        self._cursor_hide_timer = QTimer(self)
        self._cursor_hide_timer.setSingleShot(True)
        self._cursor_hide_timer.timeout.connect(self._hide_cursor)
        QApplication.instance().installEventFilter(self)
        # Pass settings to OCR panel so DictPopup has access to Anki config
        self.ocr_panel.set_settings(self._settings, main_window=self)
        # Marquee overlay — parented to scroll so it covers only the page area
        self._marquee = MarqueeOverlay(self.scroll.viewport())
        self._marquee.hide()
        self._marquee.confirmed.connect(self._on_marquee_confirmed)
        self._marquee.cancelled.connect(self._on_marquee_cancelled)
        # Bookmark state
        self._bookmarks: list[dict] = []
        self._bookmark_popup = BookmarkPopup(self)
        self._bookmark_popup.navigate.connect(self.go_to_page)
        # Marquee state
        self._captured_image_b64: str = ""
        self._marquee_callback   = None
        self._pre_marquee_ocr    = False
        # Image adjustments popup
        self._adj_popup = ImageAdjustPopup(self)
        self._adj_popup.changed.connect(self._on_adjustment_changed)
        self._adj_debounce = QTimer(self)
        self._adj_debounce.setSingleShot(True)
        self._adj_debounce.timeout.connect(self._apply_adjustment_debounced)

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
        self.scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {theme.DEFAULT_BG}; }}")

        self.page_view = PageView()
        self.page_view.ocr_requested.connect(self._run_ocr)
        self.scroll.setWidget(self.page_view)
        center_lay.addWidget(self.scroll, stretch=1)

        self.nav_bar = self._build_nav_bar()
        center_lay.addWidget(self.nav_bar)
        content_lay.addWidget(center, stretch=1)

        self.ocr_panel = OCRPanel()
        content_lay.addWidget(self.ocr_panel)

        central_lay.addWidget(content, stretch=1)
        self.statusBar().hide()  # replaced by toast overlay

    def _build_nav_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("NavBar")
        bar.setFixedHeight(34)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)

        def _nav_btn(label, slot, icon_name=None):
            b = QPushButton()
            b.setFixedHeight(20)
            b.clicked.connect(slot)
            if icon_name:
                ic = load_icon(icon_name)
                if not ic.isNull():
                    b.setIcon(ic)
                    b.setIconSize(QSize(12, 12))
                else:
                    b.setText(label)
                b.setProperty("icon_name", icon_name)
            else:
                b.setText(label)
            return b

        # ── Volume prev ──
        self.btn_vol_prev = _nav_btn("◀ Vol", self.prev_volume, "nav-vol-prev")
        self.btn_vol_prev.setToolTip("Previous volume")
        self.btn_vol_prev.hide()
        lay.addWidget(self.btn_vol_prev)

        self._vol_label = QLabel("")
        self._vol_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vol_label.setFixedWidth(62)
        self._vol_label.setStyleSheet("font-size: 8pt;")
        self._vol_label.hide()
        lay.addWidget(self._vol_label)

        # ── Page navigation ──
        self.btn_first = _nav_btn("⏮", lambda: self.go_to_page(0),             "nav-first")
        self.btn_prev  = _nav_btn("◀  Prev", self.prev_page,                    "nav-prev")
        self.btn_next  = _nav_btn("Next  ▶", self.next_page,                    "nav-next")
        self.btn_last  = _nav_btn("⏭", lambda: self.go_to_page(len(self._pages) - 1), "nav-last")

        # Stacked widget: index 0 = label, index 1 = editor
        from PyQt6.QtWidgets import QStackedWidget
        self._page_nav_stack = QStackedWidget()
        self._page_nav_stack.setFixedWidth(90)

        self.page_label = QLabel("— / —")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setToolTip("Click to jump to page")
        self.page_label.mousePressEvent = lambda _: self._start_page_jump()

        self.page_edit = QLineEdit()
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

        # ── Volume next ──
        self.btn_vol_next = _nav_btn("Vol ▶", self.next_volume, "nav-vol-next")
        self.btn_vol_next.setToolTip("Next volume")
        self.btn_vol_next.hide()
        lay.addWidget(self.btn_vol_next)

        return bar

    def _build_menu(self):
        mb = self.main_menu

        # ── Build all customisable QActions and store in self._actions ────────
        def _act(action_id: str, label: str, slot, checkable=False, checked=False):
            """Create a QAction with shortcut from settings (or default)."""
            default = self.SHORTCUT_DEFAULTS.get(action_id, ("", "", ""))[1]
            saved   = self._settings.value(f"shortcuts/{action_id}", default)
            a = QAction(label, self, checkable=checkable, checked=checked)
            if saved:
                a.setShortcut(saved)
            a.triggered.connect(slot)
            self._actions[action_id] = a
            return a

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
        open_act = _act("open_file",  "Open…",  self.open_file)
        open_dir = QAction("Open Folder…", self)
        open_dir.triggered.connect(self.open_folder)
        close_act = _act("close_file", "Close", self.close_file)
        file_menu.addActions([open_act, open_dir])
        file_menu.addSeparator()
        self._recent_menu = file_menu.addMenu("Open Recent")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(close_act)

        view_menu = mb.addMenu("View")
        fit_w    = _act("fit_width",  "Fit Width",
                        lambda: self.page_view.set_fit_mode("fit_width"))
        fit_p    = _act("fit_page",   "Fit Page",
                        lambda: self.page_view.set_fit_mode("fit_page"))
        zoom_in  = _act("zoom_in",    "Zoom In",
                        lambda: self.page_view.set_scale(self.page_view._scale * 1.2))
        zoom_out = _act("zoom_out",   "Zoom Out",
                        lambda: self.page_view.set_scale(self.page_view._scale / 1.2))

        self.act_thumbnails = _act("toggle_thumbnails", "Show Thumbnails",
                                   self._toggle_thumbnails, checkable=True, checked=True)
        self.act_ocr_panel  = _act("toggle_ocr_panel",  "Show OCR Panel",
                                   self._toggle_ocr_panel, checkable=True, checked=True)

        rtl_act = QAction("RTL (Manga)", self, checkable=True, checked=True)
        rtl_act.triggered.connect(lambda v: self._set_reading_mode("rtl" if v else "ltr"))
        fs_act = _act("fullscreen", "Toggle Fullscreen",
                      lambda: self._exit_fullscreen()
                      if self.isFullScreen() else self._enter_fullscreen())

        view_menu.addActions([fit_w, fit_p, zoom_in, zoom_out])
        view_menu.addSeparator()
        single_act = _act("single_page", "Single Page",
                          lambda: (self._set_page_mode("single"),
                                   self._actions["single_page"].setChecked(True),
                                   self._actions["double_page"].setChecked(False)),
                          checkable=True, checked=True)
        double_act = _act("double_page", "Double Page",
                          lambda: (self._set_page_mode("double"),
                                   self._actions["double_page"].setChecked(True),
                                   self._actions["single_page"].setChecked(False)),
                          checkable=True)
        self._menu_single_act = single_act
        self._menu_double_act = double_act
        view_menu.addActions([single_act, double_act])
        offset_act = _act("page_offset", "Toggle Page Offset",
                          self._toggle_page_offset, checkable=True)
        self._menu_offset_act = offset_act
        view_menu.addAction(offset_act)
        view_menu.addSeparator()
        view_menu.addActions([self.act_thumbnails, self.act_ocr_panel])
        view_menu.addSeparator()
        view_menu.addAction(rtl_act)
        view_menu.addSeparator()
        view_menu.addAction(fs_act)
        view_menu.addSeparator()
        rot_l     = _act("rotate_left",    "Rotate Left",    lambda: self._rotate(-90))
        rot_r     = _act("rotate_right",   "Rotate Right",   lambda: self._rotate(90))
        rot_reset = _act("reset_rotation", "Reset Rotation", self._reset_rotation)
        view_menu.addActions([rot_l, rot_r, rot_reset])

        nav_menu = mb.addMenu("Navigate")
        nav_menu.setMinimumWidth(230)
        prev_a = _act("prev_page",   "Previous Page", self.prev_page)
        next_a = _act("next_page",   "Next Page",     self.next_page)
        # Hidden actions for first/last (no menu entry needed)
        _act("first_page", "First Page", lambda: self.go_to_page(0))
        _act("last_page",  "Last Page",
             lambda: self.go_to_page(len(self._pages) - 1))
        nav_menu.addActions([prev_a, next_a])
        jump_act = _act("jump_to_page", "Jump to Page…", self._start_page_jump)
        nav_menu.addAction(jump_act)
        nav_menu.addSeparator()
        prev_vol_a = _act("prev_volume", "Previous Volume", self.prev_volume)
        next_vol_a = _act("next_volume", "Next Volume",     self.next_volume)
        nav_menu.addActions([prev_vol_a, next_vol_a])
        nav_menu.addSeparator()
        bm_toggle = _act("toggle_bookmark", "Toggle Bookmark",  self._toggle_bookmark)
        bm_list   = _act("show_bookmarks",  "Show Bookmarks…",  self._show_bookmarks_popup)
        nav_menu.addActions([bm_toggle, bm_list])

        ocr_menu = mb.addMenu("OCR")
        self.act_ocr_mode = _act("ocr_mode", "OCR Selection Mode",
                                 self._toggle_ocr_mode, checkable=True)
        ocr_menu.addAction(self.act_ocr_mode)
        check_ocr = QAction("Check OCR Installation…", self)
        check_ocr.triggered.connect(self._check_ocr)
        ocr_menu.addAction(check_ocr)
        ocr_menu.addSeparator()
        dict_act = _act("dict_lookup", "Dictionary Lookup",
                        lambda: self.ocr_panel.lookup_shortcut())
        ocr_menu.addAction(dict_act)

        # Actions with no menu entry (warmth, single/double already added)
        _act("toggle_warmth", "Toggle Warmth", self._toggle_warmth)

        # Add all actions to main window so Qt shortcuts fire globally
        for a in self._actions.values():
            self.addAction(a)


    def _build_toolbar(self) -> QWidget:
        """Returns a plain QWidget toolbar that slots into the outer VBox layout."""
        bar = QWidget()
        bar.setObjectName("ToolBar")
        bar.setFixedHeight(36)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(2)

        def _btn(label, slot, checkable=False, icon_name=None, tooltip=None):
            b = QPushButton(label)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            if icon_name:
                ic = load_icon(icon_name)
                if not ic.isNull():
                    b.setIcon(ic)
                    b.setIconSize(QSize(16, 16))
                    b.setText("")
                b.setProperty("icon_name", icon_name)
            if tooltip:
                b.setToolTip(tooltip)
            return b

        def _sep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedWidth(10)
            return f

        # ── Left side: thumbnails toggle ──
        self.tb_thumb_btn = _btn("", self._toggle_thumbnails, checkable=True,
                                 tooltip=f"Toggle Thumbnails ({_ctrl()}+Shift+T)")
        self.tb_thumb_btn.setChecked(True)
        lay.addWidget(self.tb_thumb_btn)
        lay.addWidget(_sep())

        # ── Centre tools ──
        lay.addWidget(_btn("📂 Open", self.open_file,
                           icon_name="open", tooltip=f"Open file ({_ctrl()}+O)"))
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
                           icon_name="zoom-in", tooltip=f"Zoom In ({_ctrl()}+=)"))
        lay.addWidget(_btn("🔍−",
                           lambda: self.page_view.set_scale(self.page_view._scale / 1.2),
                           icon_name="zoom-out", tooltip=f"Zoom Out ({_ctrl()}+-)"))
        lay.addWidget(_btn("", lambda: self._exit_fullscreen()
                           if self.isFullScreen() else self._enter_fullscreen(),
                           icon_name="fullscreen",
                           tooltip="Enter Fullscreen (F11)"))
        lay.addWidget(_sep())
        lay.addWidget(_btn("", lambda: self._rotate(-90),
                           icon_name="rotate-left",
                           tooltip="Rotate Left ([)"))
        lay.addWidget(_btn("", lambda: self._rotate(90),
                           icon_name="rotate-right",
                           tooltip="Rotate Right (])"))
        self._adj_btn = _btn("", self._show_adj_popup,
                              icon_name="adjustments",
                              tooltip="Image Adjustments")
        lay.addWidget(self._adj_btn)
        self._warm_btn = _btn("", self._toggle_warmth, checkable=True,
                               icon_name="warmth",
                               tooltip="Night Shift / Warm Filter")
        lay.addWidget(self._warm_btn)
        lay.addWidget(_sep())
        self.ocr_btn = _btn("🔤 OCR Mode", self._toggle_ocr_mode, checkable=True,
                            icon_name="ocr", tooltip=f"OCR Selection Mode ({_ctrl()}+Shift+O)")
        lay.addWidget(self.ocr_btn)

        # ── Right side: bookmarks + OCR panel toggle ──
        lay.addStretch()
        self._page_mode_btn = QPushButton("Single Page")
        self._page_mode_btn.clicked.connect(
            lambda: self._set_page_mode(
                "double" if self._page_mode == "single" else "single"
            )
        )
        lay.addWidget(self._page_mode_btn)

        # Page offset toggle (only visible in double-page mode)
        self._offset_btn = _btn("⇄", self._toggle_page_offset,
                                icon_name="page-offset",
                                tooltip="Shift page pairing — first page solo (Shift+O)")
        self._offset_btn.setCheckable(True)
        self._offset_btn.setVisible(False)
        lay.addWidget(self._offset_btn)

        lay.addWidget(_sep())
        
        # Background colour swatch
        self._bg_btn = QPushButton()
        self._bg_btn.setFixedSize(22, 22)
        self._bg_btn.setToolTip("Background colour")
        self._bg_btn.clicked.connect(self._show_bg_picker)
        lay.addWidget(self._bg_btn)

        lay.addWidget(_sep())
        self.tb_bookmark_btn = _btn("", self._toggle_bookmark, checkable=True,
                                    icon_name="bookmark-off",
                                    tooltip=f"Bookmark this page ({_ctrl()}+B)")
        lay.addWidget(self.tb_bookmark_btn)
        self.tb_bookmarks_btn = _btn("", self._show_bookmarks_popup,
                                     icon_name="bookmarks",
                                     tooltip=f"Show all bookmarks ({_ctrl()}+Shift+B)")
        lay.addWidget(self.tb_bookmarks_btn)
        
        lay.addWidget(_sep())
        self.tb_ocr_btn = _btn("", self._toggle_ocr_panel, checkable=True,
                               tooltip=f"Toggle OCR Panel ({_ctrl()}+Shift+P)")
        self.tb_ocr_btn.setChecked(True)
        lay.addWidget(self.tb_ocr_btn)

        # Insert at top of central layout (index 0), above content area
        self._toolbar = bar
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

    # ─────────────────────────────────────────────────────────────────────────
    # Recent files
    # ─────────────────────────────────────────────────────────────────────────

    _MAX_RECENT = 10

    def _push_recent(self, path: str):
        import json as _json
        raw = self._settings.value("recent_files", "[]")
        try:
            recent = _json.loads(raw)
        except Exception:
            recent = []
        path = str(Path(path).resolve())
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:self._MAX_RECENT]
        self._settings.setValue("recent_files", _json.dumps(recent))
        self._rebuild_recent_menu()

    def _get_recent(self) -> list:
        import json as _json
        raw = self._settings.value("recent_files", "[]")
        try:
            return _json.loads(raw)
        except Exception:
            return []

    def _rebuild_recent_menu(self):
        self._recent_menu.clear()
        recent = self._get_recent()
        if not recent:
            empty = QAction("No recent files", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for path in recent:
            p = Path(path)
            act = QAction(p.name, self)
            act.setToolTip(path)
            act.triggered.connect(lambda _, p=path: self._open_recent(p))
            self._recent_menu.addAction(act)
        self._recent_menu.addSeparator()
        clear_act = QAction("Clear Recent Files", self)
        clear_act.triggered.connect(self._clear_recent)
        self._recent_menu.addAction(clear_act)

    def _open_recent(self, path: str):
        if not Path(path).exists():
            QMessageBox.warning(self, "File Not Found",
                                f"Could not find:\n{path}\n\nRemoving from recent list.")
            import json as _json
            recent = self._get_recent()
            if path in recent:
                recent.remove(path)
            self._settings.setValue("recent_files", _json.dumps(recent))
            self._rebuild_recent_menu()
            return
        self._load_path(path)

    def _clear_recent(self):
        self._settings.setValue("recent_files", "[]")
        self._rebuild_recent_menu()

    # ─────────────────────────────────────────────────────────────────────────
    # Background colour
    # ─────────────────────────────────────────────────────────────────────────


    def _apply_bg_colour(self, colour: str):
        """Apply background colour to the page scroll area and persist it."""
        self._settings.setValue("ui/bg_colour", colour)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {colour}; }}"
        )
        self.page_view.setStyleSheet(f"background-color: {colour};")
        # Update swatch button
        self._bg_btn.setStyleSheet(theme.bg_swatch_stylesheet(colour))

    def _show_bg_picker(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {theme._active['menu_bg']}; color: {theme._active['text']};
                     border: 1px solid {theme._active['border']}; }}
            QMenu::item:selected {{ background: {theme.ACCENT}; color: #fff; }}
        """)
        for name, hex_col in theme.BG_PRESETS:
            act = QAction(name, self)
            # Show a coloured block next to the name
            px = QPixmap(12, 12)
            from PyQt6.QtGui import QColor
            px.fill(QColor(hex_col))
            act.setIcon(QIcon(px))
            act.triggered.connect(lambda _, c=hex_col: self._apply_bg_colour(c))
            menu.addAction(act)
        menu.addSeparator()
        custom_act = QAction("Custom…", self)
        custom_act.triggered.connect(self._pick_custom_bg)
        menu.addAction(custom_act)
        # Show below the button
        btn_pos = self._bg_btn.mapToGlobal(
            QPoint(0, self._bg_btn.height())
        )
        menu.exec(btn_pos)

    def _pick_custom_bg(self):
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        current = self._settings.value("ui/bg_colour", theme.DEFAULT_BG)
        colour = QColorDialog.getColor(QColor(current), self, "Choose Background Colour")
        if colour.isValid():
            self._apply_bg_colour(colour.name())

    def close_file(self):
        """Close the current file and return to blank state."""
        self._pages        = []
        self._current      = 0
        self._current_file = ""
        self._bookmarks    = []
        self._series       = None
        self._page_offset  = 0
        self._spreads      = []
        self._at_volume_boundary = False
        self.page_view.set_pixmap(QPixmap())
        self.thumb_list.clear()
        self.page_label.setText("— / —")
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_first.setEnabled(False)
        self.btn_last.setEnabled(False)
        self._update_series_ui()
        if hasattr(self, "_offset_btn"):
            self._offset_btn.setChecked(False)
            self._offset_btn.setVisible(False)
        if hasattr(self, "_menu_offset_act"):
            self._menu_offset_act.setChecked(False)
        self.ocr_panel.clear_all()
        self.setWindowTitle("Tako Reader — タコReader")
        self._set_keep_awake(False)

    def open_file(self):
        last_dir = self._settings.value("last_dir", "")
        path, _  = QFileDialog.getOpenFileName(
            self, "Open Manga File", last_dir,
            "Manga Files (*.cbz *.cbr *.cb7 *.cbt *.zip *.rar *.7z *.tar *.pdf *.jpg *.jpeg *.png *.webp *.bmp);;All Files (*)"
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
        # Enrich title with series position
        if self._series and self._series.has_series:
            self.setWindowTitle(
                f"Tako Reader — {self._series.series_name} — "
                f"{self._series.label()} — {Path(path).name}"
            )
        if self._settings.value("general/keep_awake", True, type=bool):
            self._set_keep_awake(True)
        self._current_file = str(Path(path).resolve())
        self._bookmarks    = self._load_bookmarks()
        self._rotation     = self._load_rotation()
        self._adjustments  = self._load_adjustments()
        self._page_offset  = self._load_page_offset()
        self._adj_cache.clear()
        self._at_volume_boundary = False
        self._compute_spreads()

        # Sync offset button state
        if hasattr(self, "_offset_btn"):
            self._offset_btn.setChecked(self._page_offset == 1)
            self._offset_btn.setVisible(self._page_mode == "double")

        # Series detection — scan sibling files for volume navigation
        resolved = Path(path).resolve()
        if resolved.is_file():
            self._series = SeriesContext(resolved)
        else:
            self._series = None
        self._update_series_ui()
        if self._settings.value("ocr/clear_on_file", True, type=bool):
            self.ocr_panel.clear_all()

        self.thumb_list.load_pages(pages)
        self._settings.setValue("session/last_file", str(Path(path).resolve()))
        self._push_recent(path)
        # Sync adjustment popup sliders to loaded values
        if hasattr(self, "_adj_popup"):
            self._adj_popup.load_values(**self._adjustments)
        # Jump to last-read page if session memory is enabled
        last = self._load_last_page()
        if self._settings.value("general/session_memory", True, type=bool) and last > 0:
            self.go_to_page(0)   # render first so thumbnails are set
            self.go_to_page(last)
        else:
            self.go_to_page(0)
        

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_adjustments(self, px: QPixmap) -> QPixmap:
        """Apply brightness/contrast/saturation/sharpness via numpy — no encode/decode."""
        adj = self._adjustments
        defaults = {"brightness": 100, "contrast": 100, "saturation": 100,
                    "sharpness": 100, "warmth": 0}
        if all(adj.get(k, d) == d for k, d in defaults.items()):
            return px
        # Check cache
        cache_key = (px.cacheKey(),
                     adj["brightness"], adj["contrast"],
                     adj["saturation"], adj["sharpness"], adj.get("warmth", 0))
        cached = self._adj_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            import numpy as np
            from PyQt6.QtGui import QImage

            img = px.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            ptr = img.bits()
            ptr.setsize(img.width() * img.height() * 4)
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
                (img.height(), img.width(), 4)
            ).copy().astype(np.float32)

            rgb = arr[:, :, :3]
            alpha = arr[:, :, 3:4]

            # Brightness: simple scale
            b = adj["brightness"] / 100.0
            rgb = rgb * b

            # Contrast: scale around mid-grey (127.5)
            c = adj["contrast"] / 100.0
            rgb = (rgb - 127.5) * c + 127.5

            # Saturation: lerp between greyscale and colour
            s = adj["saturation"] / 100.0
            grey = rgb[:, :, 0:1] * 0.299 + rgb[:, :, 1:2] * 0.587 + rgb[:, :, 2:3] * 0.114
            rgb = grey + (rgb - grey) * s

            # Sharpness: blend original with unsharp mask
            sh = adj["sharpness"] / 100.0
            if sh != 1.0:
                from PIL import Image, ImageFilter
                import io
                # Only need sharpness via PIL — work on small array
                rgb_clipped = np.clip(rgb, 0, 255).astype(np.uint8)
                pil_img = Image.fromarray(rgb_clipped, "RGB")
                if sh > 1.0:
                    # Sharpen: blend with unsharp mask
                    blurred = np.array(
                        pil_img.filter(ImageFilter.GaussianBlur(radius=1)),
                        dtype=np.float32
                    )
                    rgb = rgb_clipped.astype(np.float32) + (
                        rgb_clipped.astype(np.float32) - blurred
                    ) * (sh - 1.0)
                else:
                    # Soften: blend with blurred
                    blurred = np.array(
                        pil_img.filter(ImageFilter.GaussianBlur(radius=2)),
                        dtype=np.float32
                    )
                    rgb = rgb_clipped.astype(np.float32) * sh + blurred * (1.0 - sh)

            # Warmth: boost red, slightly boost green, reduce blue, dim slightly
            w = adj.get("warmth", 0) / 100.0
            if w > 0:
                rgb = rgb.astype(np.float32)
                # Dim: multiply by (1 - w*0.15) so at 100% brightness drops ~15%
                rgb = rgb * (1.0 - w * 0.15)
                rgb[:, :, 0] = np.clip(rgb[:, :, 0] + w * 40,  0, 255)  # R +40 at full
                rgb[:, :, 1] = np.clip(rgb[:, :, 1] + w * 10,  0, 255)  # G +10 at full
                rgb[:, :, 2] = np.clip(rgb[:, :, 2] - w * 30,  0, 255)  # B -30 at full

            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
            result_arr = np.concatenate([rgb, alpha.astype(np.uint8)], axis=2)
            result_arr = np.ascontiguousarray(result_arr)

            h, w = result_arr.shape[:2]
            result_img = QImage(
                result_arr.tobytes(), w, h, w * 4,
                QImage.Format.Format_RGBA8888
            )
            result_px = QPixmap.fromImage(result_img)
            result_px.setDevicePixelRatio(px.devicePixelRatio())
            # Cache result (keep max 20 entries to avoid unbounded memory use)
            if len(self._adj_cache) > 20:
                self._adj_cache.pop(next(iter(self._adj_cache)))
            self._adj_cache[cache_key] = result_px
            return result_px
        except Exception as e:
            print(f"[adjustments error] {e}")
            return px

    def _rotate_pixmap(self, px: QPixmap) -> QPixmap:
        """Rotate a pixmap by self._rotation degrees."""
        if self._rotation == 0:
            return px
        transform = QTransform().rotate(self._rotation)
        return px.transformed(transform, Qt.TransformationMode.SmoothTransformation)

    # ─────────────────────────────────────────────────────────────────────────
    # Double-page spread computation
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_spreads(self):
        """Build the list of page spreads for double-page mode.
        Each spread is a tuple of 1 or 2 page indices.
        Solo pages: cover with offset, wide (landscape) pages, last odd page.
        """
        if not self._pages or self._page_mode != "double":
            self._spreads = [(i,) for i in range(len(self._pages))]
            return

        auto_wide = self._settings.value("view/auto_spread", True, type=bool)
        spreads = []
        i = 0
        n = len(self._pages)

        while i < n:
            # First page is solo when offset is on
            if i == 0 and self._page_offset == 1:
                spreads.append((i,))
                i += 1
                continue

            # Wide page (width >= height) → always solo
            if auto_wide:
                px = self._pages[i]
                if px.width() >= px.height():
                    spreads.append((i,))
                    i += 1
                    continue

            # Last page with no partner → solo
            if i + 1 >= n:
                spreads.append((i,))
                i += 1
                continue

            # Check if next page is wide → current becomes solo
            if auto_wide and i + 1 < n:
                px_next = self._pages[i + 1]
                if px_next.width() >= px_next.height():
                    spreads.append((i,))
                    i += 1
                    continue

            # Normal pair
            spreads.append((i, i + 1))
            i += 2

        self._spreads = spreads

    def _spread_for_page(self, page_idx: int) -> tuple[int, ...]:
        """Return the spread tuple containing page_idx."""
        for spread in self._spreads:
            if page_idx in spread:
                return spread
        # Fallback: solo
        return (page_idx,)

    def _spread_index(self, page_idx: int) -> int:
        """Return the index into _spreads that contains page_idx."""
        for si, spread in enumerate(self._spreads):
            if page_idx in spread:
                return si
        return 0

    def _get_display_pixmap(self, index: int) -> QPixmap:
        """Return a single or side-by-side double-page pixmap, rotated and adjusted."""
        px1 = self._apply_adjustments(self._pages[index])

        if self._page_mode != "double":
            return self._rotate_pixmap(px1)

        # Use precomputed spread to decide what to show
        spread = self._spread_for_page(index)
        if len(spread) == 1:
            # Solo page (cover, wide, or last odd)
            return self._rotate_pixmap(px1)

        # Double spread — render both pages
        idx_a, idx_b = spread
        pxa = self._apply_adjustments(self._pages[idx_a])
        pxb = self._apply_adjustments(self._pages[idx_b])
        # In RTL mode page order is right-to-left
        left, right = (pxb, pxa) if self._reading_mode == "rtl" else (pxa, pxb)
        h = max(left.height(), right.height())
        combined = QPixmap(left.width() + right.width(), h)
        combined.setDevicePixelRatio(pxa.devicePixelRatio())
        combined.fill(Qt.GlobalColor.transparent)
        painter = QPainter(combined)
        painter.drawPixmap(0,            (h - left.height())  // 2, left)
        painter.drawPixmap(left.width(), (h - right.height()) // 2, right)
        painter.end()
        return self._rotate_pixmap(combined)

    def _preload_pages(self, current: int):
        """Warm the adj cache for upcoming pages in the background."""
        if not self._settings.value("general/preload", True, type=bool):
            return
        count = self._settings.value("general/preload_count", 2, type=int)
        step  = -1 if self._reading_mode == "rtl" else 1
        indices = []
        for i in range(1, count + 1):
            nxt = current + step * i
            if 0 <= nxt < len(self._pages):
                indices.append(nxt)
        if not indices:
            return
        worker = PagePreloadWorker(indices, self._pages, self._get_display_pixmap)
        if not hasattr(self, "_preload_workers"):
            self._preload_workers = set()
        self._preload_workers.add(worker)
        worker.done.connect(lambda: self._preload_workers.discard(worker))
        worker.start()

    def go_to_page(self, index: int):
        if not self._pages:
            return
        index = max(0, min(index, len(self._pages) - 1))
        # In double mode, snap to the first page of the containing spread
        if self._page_mode == "double" and self._spreads:
            spread = self._spread_for_page(index)
            index = spread[0]
        self._current = index
        self._at_volume_boundary = False
        self.page_view.set_pixmap(self._get_display_pixmap(index))
        self.thumb_list.select_page(index)
        # Page label: show spread range in double mode
        if self._page_mode == "double" and self._spreads:
            spread = self._spread_for_page(index)
            if len(spread) == 2:
                self.page_label.setText(f"{spread[0]+1}-{spread[1]+1} / {len(self._pages)}")
            else:
                self.page_label.setText(f"{index+1} / {len(self._pages)}")
        else:
            self.page_label.setText(f"{index+1} / {len(self._pages)}")
        self.btn_prev.setEnabled(index > 0 or bool(self._series and not self._series.is_first))
        self.btn_next.setEnabled(index < len(self._pages) - 1 or bool(self._series and not self._series.is_last))
        self.btn_first.setEnabled(index > 0)
        self.btn_last.setEnabled(index < len(self._pages) - 1)
        self._save_session_page(index)
        if hasattr(self, "tb_bookmark_btn"):
            self._update_bookmark_btn()
        self._preload_pages(index)

    def prev_page(self):
        if self._page_mode == "double" and self._spreads:
            si = self._spread_index(self._current)
            # RTL reverses direction: "prev" goes forward in page index
            target_si = si + 1 if self._reading_mode == "rtl" else si - 1
            if 0 <= target_si < len(self._spreads):
                self.go_to_page(self._spreads[target_si][0])
                return
            # At boundary
            target = -1 if self._reading_mode != "rtl" else len(self._pages)
        else:
            target = self._current + (1 if self._reading_mode == "rtl" else -1)

        if target < 0:
            if self._series and self._series.prev_path:
                if self._at_volume_boundary:
                    self.prev_volume()
                    return
                self._at_volume_boundary = True
                self._toast("Beginning of volume — press again for previous volume")
                return
            return
        if target >= len(self._pages):
            if self._series and self._series.next_path:
                if self._at_volume_boundary:
                    self.next_volume()
                    return
                self._at_volume_boundary = True
                self._toast("End of volume — press again for next volume")
                return
            return
        self.go_to_page(target)

    def next_page(self):
        if self._page_mode == "double" and self._spreads:
            si = self._spread_index(self._current)
            # RTL reverses direction: "next" goes backward in page index
            target_si = si - 1 if self._reading_mode == "rtl" else si + 1
            if 0 <= target_si < len(self._spreads):
                self.go_to_page(self._spreads[target_si][0])
                return
            # At boundary
            target = len(self._pages) if self._reading_mode != "rtl" else -1
        else:
            target = self._current + (-1 if self._reading_mode == "rtl" else 1)

        if target >= len(self._pages):
            if self._series and self._series.next_path:
                if self._at_volume_boundary:
                    self.next_volume()
                    return
                self._at_volume_boundary = True
                self._toast("End of volume — press again for next volume")
                return
            return
        if target < 0:
            if self._series and self._series.prev_path:
                if self._at_volume_boundary:
                    self.prev_volume()
                    return
                self._at_volume_boundary = True
                self._toast("Beginning of volume — press again for previous volume")
                return
            return
        self.go_to_page(target)

    # ─────────────────────────────────────────────────────────────────────────
    # Volume / series navigation
    # ─────────────────────────────────────────────────────────────────────────

    def prev_volume(self):
        """Load the previous volume in the series."""
        if not self._series or not self._series.prev_path:
            return
        path = str(self._series.prev_path)
        self._load_path(path)
        # Jump to last page so user can read backwards seamlessly
        if self._pages:
            self.go_to_page(len(self._pages) - 1)

    def next_volume(self):
        """Load the next volume in the series."""
        if not self._series or not self._series.next_path:
            return
        self._load_path(str(self._series.next_path))

    def _update_series_ui(self):
        """Show or hide volume navigation based on series context."""
        has = self._series is not None and self._series.has_series
        self.btn_vol_prev.setVisible(has)
        self.btn_vol_next.setVisible(has)
        self._vol_label.setVisible(has)
        if has:
            self.btn_vol_prev.setEnabled(not self._series.is_first)
            self.btn_vol_next.setEnabled(not self._series.is_last)
            self._vol_label.setText(self._series.label())
            self._vol_label.setToolTip(self._series.series_name)

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
        QTimer.singleShot(0, self.page_view._apply_fit)

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
        QTimer.singleShot(0, self.page_view._apply_fit)

    def _set_page_mode(self, mode: str):
        self._page_mode = mode
        self._compute_spreads()
        if self._pages:
            self.go_to_page(self._current)
        self._toast(f"Page mode: {mode.capitalize()}", 2000)
        # Sync toolbar button text
        if hasattr(self, "_page_mode_btn"):
            self._page_mode_btn.setText(
                "Double Page" if mode == "double" else "Single Page"
            )
        # Show/hide offset toggle — only relevant in double mode
        if hasattr(self, "_offset_btn"):
            self._offset_btn.setVisible(mode == "double")
        # Sync menu actions
        if hasattr(self, "_menu_single_act"):
            self._menu_single_act.setChecked(mode == "single")
            self._menu_double_act.setChecked(mode == "double")

    def _toggle_page_offset(self):
        """Toggle whether the first page displays solo (shifting all pairings by 1)."""
        self._page_offset = 0 if self._page_offset else 1
        self._save_page_offset()
        self._compute_spreads()
        if hasattr(self, "_offset_btn"):
            self._offset_btn.setChecked(self._page_offset == 1)
        if hasattr(self, "_menu_offset_act"):
            self._menu_offset_act.setChecked(self._page_offset == 1)
        if self._pages:
            self.go_to_page(self._current)
        self._toast(
            "Page offset: ON (first page solo)" if self._page_offset
            else "Page offset: OFF",
            2000
        )

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
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.MouseMove and self._pages:
            # Only auto-hide cursor when moving over the page canvas
            if obj is self.scroll.viewport() or obj is self.page_view:
                self._reset_cursor_timer()
            elif self._cursor_hidden:
                # Moved off the canvas — restore immediately
                self._restore_canvas_cursor()
                self._cursor_hide_timer.stop()
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            # Page-edit escape/focus-out handling
            if obj is self.page_edit:
                if key == Qt.Key.Key_Escape:
                    self._page_nav_stack.setCurrentIndex(0)
                    return True
            # Navigation keys — only intercept when fullscreen, so arrow keys
            # work normally in text fields (bookmarks, Anki edit, etc.) otherwise.
            if self.isFullScreen() and self._page_nav_stack.currentIndex() == 0:
                if key in (Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_N):
                    self.next_page()
                    return True
                elif key in (Qt.Key.Key_Left, Qt.Key.Key_B, Qt.Key.Key_P):
                    self.prev_page()
                    return True
                elif key == Qt.Key.Key_Home:
                    self.go_to_page(0)
                    return True
                elif key == Qt.Key.Key_End and self._pages:
                    self.go_to_page(len(self._pages) - 1)
                    return True
                elif key == Qt.Key.Key_F11:
                    self._exit_fullscreen() if self.isFullScreen() else self._enter_fullscreen()
                    return True
                elif key == Qt.Key.Key_Escape and self.isFullScreen():
                    self._exit_fullscreen()
                    return True
        elif event.type() == QEvent.Type.FocusOut and obj is self.page_edit:
            self._page_nav_stack.setCurrentIndex(0)
        return super().eventFilter(obj, event)

    def _enter_fullscreen(self):
        """Hide all chrome and go fullscreen."""
        # Snapshot current visibility so we can restore it exactly
        self._pre_fs = {
            "toolbar":   self._toolbar.isVisible(),
            "nav_bar":   self.nav_bar.isVisible(),
            "thumb":     self.thumb_list.isVisible(),
            "ocr_panel": self.ocr_panel.isVisible(),
            "menubar":   self.menuBar().isVisible(),
        }
        self._toolbar.hide()
        self.nav_bar.hide()
        self.thumb_list.hide()
        self.ocr_panel.hide()
        self.menuBar().hide()
        self.showFullScreen()
        QTimer.singleShot(50, self.page_view._apply_fit)
        self._show_fs_toast()

    def _exit_fullscreen(self):
        """Restore all chrome and exit fullscreen."""
        self.showNormal()
        pre = getattr(self, "_pre_fs", {})
        self._toolbar.setVisible(   pre.get("toolbar",   True))
        self.nav_bar.setVisible(    pre.get("nav_bar",   True))
        self.thumb_list.setVisible( pre.get("thumb",     True))
        self.ocr_panel.setVisible(  pre.get("ocr_panel", True))
        self.menuBar().setVisible(  pre.get("menubar",   True))
        QTimer.singleShot(50, self.page_view._apply_fit)

    def _toast(self, message: str, duration_ms: int = 2500):
        """Show a message overlaid on the page view. Replaces any existing toast."""
        # Kill any existing toast immediately
        if hasattr(self, "_current_toast") and self._current_toast:
            try:
                self._current_toast.deleteLater()
            except Exception:
                pass
        if hasattr(self, "_current_toast_timer") and self._current_toast_timer:
            self._current_toast_timer.stop()

        # Parent to scroll area so position is relative to the page view
        toast = QLabel(message, self.scroll)
        toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toast.setStyleSheet(theme.toast_stylesheet())
        toast.adjustSize()
        sw = self.scroll.width()
        sh = self.scroll.height()
        x = (sw - toast.width())  // 2
        y =  sh - toast.height() - 32
        toast.move(x, y)
        toast.show()
        toast.raise_()
        self._current_toast = toast

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._dismiss_toast)
        timer.start(duration_ms)
        self._current_toast_timer = timer

    def _dismiss_toast(self):
        """Dismiss the current toast immediately."""
        if hasattr(self, "_current_toast") and self._current_toast:
            try:
                self._current_toast.deleteLater()
            except Exception:
                pass
            self._current_toast = None
        if hasattr(self, "_current_toast_timer") and self._current_toast_timer:
            self._current_toast_timer.stop()
            self._current_toast_timer = None

    def _show_fs_toast(self):
        self._toast("Press F11 or Esc to exit fullscreen", 3000)

    def _rotate(self, delta: int):
        """Rotate by delta degrees (90 or -90) and redraw."""
        self._rotation = (self._rotation + delta) % 360
        self._save_rotation()
        if self._pages:
            self.page_view.set_pixmap(self._get_display_pixmap(self._current))

    def _on_adjustment_changed(self):
        """Debounce rapid slider ticks — wait 60ms after last change then redraw."""
        self._adjustments = self._adj_popup.get_values()
        # Invalidate cache whenever values change
        self._adj_cache.clear()
        if self._adj_debounce:
            self._adj_debounce.start(60)

    def _apply_adjustment_debounced(self):
        """Called 60ms after the last slider movement — persist and redraw."""
        self._save_adjustments()
        # Sync warm toolbar button to slider state
        if hasattr(self, "_warm_btn"):
            self._warm_btn.setChecked(self._adjustments.get("warmth", 0) > 0)
        if self._pages:
            self.page_view.set_pixmap(self._get_display_pixmap(self._current))

    def _toggle_warmth(self):
        """Toolbar toggle: turn warmth off (remember value) or restore last value."""
        current = self._adjustments.get("warmth", 0)
        if current > 0:
            # Turning off — remember the intensity
            self._last_warmth = current
            self._adjustments["warmth"] = 0
            self._warm_btn.setChecked(False)
        else:
            # Turning on — restore last intensity, or default 50
            v = getattr(self, "_last_warmth", 50)
            self._adjustments["warmth"] = v
            self._warm_btn.setChecked(True)
        # Sync slider and label in popup (blockSignals prevents _on_change firing)
        v = self._adjustments["warmth"]
        self._adj_popup._sliders["warmth"].blockSignals(True)
        self._adj_popup._sliders["warmth"].setValue(v)
        self._adj_popup._sliders["warmth"].blockSignals(False)
        self._adj_popup._val_labels["warmth"].setText(f"{v}%")
        self._adj_popup.warmth = v
        self._adj_cache.clear()
        self._save_adjustments()
        if self._pages:
            self.page_view.set_pixmap(self._get_display_pixmap(self._current))

    def _show_adj_popup(self):
        self._adj_popup.load_values(**self._adjustments)
        btn_pos = self._adj_btn.mapToGlobal(
            QPoint(self._adj_btn.width() // 2, self._adj_btn.height())
        )
        self._adj_popup.show_at(btn_pos)

    def _reset_rotation(self):
        self._rotation = 0
        self._save_rotation()
        if self._pages:
            self.page_view.set_pixmap(self._get_display_pixmap(self._current))

    def _adj_key(self) -> str:
        import hashlib
        h = hashlib.md5(self._current_file.encode()).hexdigest()[:12]
        return f"adjustments/{h}"

    def _load_adjustments(self) -> dict:
        if not self._current_file:
            return {"brightness": 100, "contrast": 100, "saturation": 100, "sharpness": 100}
        import json as _json
        raw = self._settings.value(self._adj_key(), "{}")
        try:
            saved = _json.loads(raw)
            defaults = {"brightness": 100, "contrast": 100, "saturation": 100,
                        "sharpness": 100, "warmth": 0}
            defaults.update(saved)
            return defaults
        except Exception:
            return {"brightness": 100, "contrast": 100, "saturation": 100,
                    "sharpness": 100, "warmth": 0}

    def _save_adjustments(self):
        if self._current_file:
            import json as _json
            self._settings.setValue(self._adj_key(), _json.dumps(self._adjustments))

    def _rot_key(self) -> str:
        import hashlib
        h = hashlib.md5(self._current_file.encode()).hexdigest()[:12]
        return f"rotation/{h}"

    def _load_rotation(self) -> int:
        if not self._current_file:
            return 0
        return self._settings.value(self._rot_key(), 0, type=int)

    def _save_rotation(self):
        if self._current_file:
            self._settings.setValue(self._rot_key(), self._rotation)

    def _offset_key(self) -> str:
        import hashlib
        h = hashlib.md5(self._current_file.encode()).hexdigest()[:12]
        return f"page_offset/{h}"

    def _load_page_offset(self) -> int:
        if not self._current_file:
            return 0
        return self._settings.value(self._offset_key(), 0, type=int)

    def _save_page_offset(self):
        if self._current_file:
            self._settings.setValue(self._offset_key(), self._page_offset)

    def _set_reading_mode(self, mode: str):
        self._reading_mode = mode
        self._toast(f"Reading mode: {'Right→Left (Manga)' if mode == 'rtl' else 'Left→Right'}")

    # ─────────────────────────────────────────────────────────────────────────
    # OCR
    # ─────────────────────────────────────────────────────────────────────────

    def _toggle_ocr_mode(self, checked: bool | None = None):
        if checked is None:
            checked = not self.act_ocr_mode.isChecked()
        self.act_ocr_mode.setChecked(checked)
        self.ocr_btn.setChecked(checked)
        self.page_view.set_ocr_mode(checked)
        self._toast("OCR mode: drag to select text region on page" if checked else "OCR mode off")

    def _run_ocr(self, image: QImage, rect: QRect):
        if self._ocr_worker and self._ocr_worker.isRunning():
            return
        device = self._settings.value("ocr/device", "cpu")
        # Check if the model is loaded yet — show loading indicator if not
        if is_frozen():
            model = _InProcessModel.get(device)
            if not model._ready:
                self.ocr_panel.set_ocr_state("loading")
        else:
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

    # ─────────────────────────────────────────────────────────────────────────
    # Image capture via marquee
    # ─────────────────────────────────────────────────────────────────────────

    def _image_field_is_mapped(self) -> bool:
        s = self._settings
        s.beginGroup("anki/field")
        keys = s.childKeys()
        s.endGroup()
        for field_name in keys:
            if s.value(f"anki/field/{field_name}", "— skip —") == "Image":
                return True
        return False

    def enter_marquee_mode(self, callback):
        """
        Activate the marquee overlay. callback(b64_str | "") is called
        with the captured image (base64 PNG) or "" if cancelled.
        """
        if not self._marquee:
            callback("")
            return
        self._marquee_callback  = callback
        self._pre_marquee_ocr   = self.page_view._ocr_mode
        if self._pre_marquee_ocr:
            self._toggle_ocr_mode(False)
        self._marquee.activate(cover_widget=self.scroll.viewport())
        self._toast("Draw a selection for the image field — press Esc to skip",
                    60000)

    def _on_marquee_confirmed(self, rect: QRect):
        """User confirmed a selection — crop from source pixmap and encode."""
        b64 = ""
        try:
            px = self.page_view._pixmap_orig
            if px:
                dpr      = self.page_view.devicePixelRatio()
                scale    = self.page_view._scale
                pm       = self.page_view.pixmap()
                # Logical size of the displayed pixmap
                pm_lw    = pm.width()  / dpr if pm else px.width()  * scale
                pm_lh    = pm.height() / dpr if pm else px.height() * scale
                # Centering offset within page_view
                off_x    = (self.page_view.width()  - pm_lw) / 2
                off_y    = (self.page_view.height() - pm_lh) / 2
                h_scroll = self.scroll.horizontalScrollBar().value()
                v_scroll = self.scroll.verticalScrollBar().value()

                # rect comes from mouse events inside the overlay.
                # The overlay is a top-level window whose (0,0) == viewport (0,0).
                # To get source image coords:
                #   1. Add scroll offset (page may be scrolled)
                #   2. Subtract centering offset (pixmap may not fill page_view)
                # Overlay is a child widget of the viewport — coords are viewport-local.
                # _pixmap_orig has DPR=1 (loaded directly from file).
                # scale = logical screen px / source px.
                # source px = (viewport-local px + scroll - centering offset) / scale
                src_rect = QRect(
                    int((rect.x() + h_scroll - off_x) / scale),
                    int((rect.y() + v_scroll - off_y) / scale),
                    int(rect.width()  / scale),
                    int(rect.height() / scale),
                ).intersected(QRect(0, 0, px.width(), px.height()))

                if src_rect.isValid():
                    cropped = px.copy(src_rect)
                    from PyQt6.QtCore import QBuffer, QIODevice
                    import base64
                    buf = QBuffer()
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    cropped.save(buf, "PNG")
                    buf.close()
                    b64 = base64.b64encode(bytes(buf.data())).decode()
        except Exception as e:
            print(f"[marquee capture error] {e}")
        self._restore_after_marquee(b64)

    def _on_marquee_cancelled(self):
        self._restore_after_marquee("")

    def _restore_after_marquee(self, b64: str):
        self._dismiss_toast()
        # Restore OCR mode
        if self._pre_marquee_ocr:
            self._toggle_ocr_mode(True)
        cb = self._marquee_callback
        self._marquee_callback = None
        if cb:
            cb(b64)

    def _apply_shortcuts(self):
        """Read shortcuts from QSettings and apply to all registered QActions."""
        for action_id, action in self._actions.items():
            default = self.SHORTCUT_DEFAULTS.get(action_id, ("","",""))[1]
            saved   = self._settings.value(f"shortcuts/{action_id}", default)
            action.setShortcut(saved if saved else "")

    # ─────────────────────────────────────────────────────────────────────────
    # Keep-awake / screen inhibit
    # ─────────────────────────────────────────────────────────────────────────

    def _set_keep_awake(self, enable: bool):
        import platform
        if platform.system() == "Darwin":
            if enable and not self._keep_awake_active:
                import subprocess
                self._caffeinate = subprocess.Popen(
                    ["caffeinate", "-d", "-i"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self._keep_awake_active = True
            elif not enable and self._keep_awake_active:
                if hasattr(self, "_caffeinate"):
                    self._caffeinate.terminate()
                self._keep_awake_active = False
        elif platform.system() == "Windows":
            try:
                import ctypes
                ES_CONTINUOUS       = 0x80000000
                ES_DISPLAY_REQUIRED = 0x00000002
                ES_SYSTEM_REQUIRED  = 0x00000001
                if enable:
                    ctypes.windll.kernel32.SetThreadExecutionState(
                        ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
                    )
                else:
                    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                self._keep_awake_active = enable
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # Auto-hide cursor
    # ─────────────────────────────────────────────────────────────────────────

    def _reset_cursor_timer(self):
        if self._cursor_hidden:
            self._restore_canvas_cursor()
        self._cursor_hide_timer.start(2000)

    def _hide_cursor(self):
        if not self._pages:
            return
        # Only blank the cursor over the page canvas, not the whole app
        self.scroll.viewport().setCursor(Qt.CursorShape.BlankCursor)
        self.page_view.setCursor(Qt.CursorShape.BlankCursor)
        self._cursor_hidden = True

    def _restore_canvas_cursor(self):
        """Restore the normal cursor on the page canvas."""
        self.scroll.viewport().unsetCursor()
        self.page_view._update_cursor()  # restores arrow/crosshair based on OCR mode
        self._cursor_hidden = False

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
        msg.setStyleSheet(f"QMessageBox {{ background: {theme._active['window_bg']}; color: {theme._active['text']}; }}"
                          f"QLabel {{ color: {theme._active['text']}; }}")
        msg.exec()

    def open_settings(self):
        # Snapshot current theme/accent so we can detect changes
        old_theme  = self._settings.value("ui/theme",  "dark")
        old_accent = self._settings.value("ui/accent", theme.DEFAULT_ACCENT)

        dlg = SettingsDialog(self._settings,
                             shortcut_defaults=self.SHORTCUT_DEFAULTS,
                             parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Restart any cached OCR backend so next call uses the new device
            new_device = self._settings.value("ocr/device", "cpu")
            # Shut down any backends for a different device
            for dev in list(OCRProcessManager._instances.keys()):
                if dev != new_device:
                    OCRProcessManager._instances[dev]._stop()
                    del OCRProcessManager._instances[dev]
            for dev in list(_InProcessModel._instances.keys()):
                if dev != new_device:
                    del _InProcessModel._instances[dev]
            # Apply updated shortcuts to all actions
            self._apply_shortcuts()
            # Check for theme/accent changes
            new_theme  = self._settings.value("ui/theme",  "dark")
            new_accent = self._settings.value("ui/accent", theme.DEFAULT_ACCENT)
            if new_theme != old_theme or new_accent != old_accent:
                self._refresh_theme()
            self._toast(f"Settings saved — OCR device: {new_device}")

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
            if self.isFullScreen():
                self._exit_fullscreen()
            else:
                self._enter_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self._exit_fullscreen()
        else:
            super().keyPressEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # Theme
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_theme(self):
        """Apply the current theme + accent from QSettings."""
        tid    = self._settings.value("ui/theme",  "dark")
        accent = self._settings.value("ui/accent", theme.DEFAULT_ACCENT)
        theme.apply_theme(tid, accent)
        self.setStyleSheet(theme.APP_STYLESHEET)

    def _refresh_theme(self):
        """Re-apply the active theme to every widget after a theme change."""
        self._apply_theme()
        # Scroll area and page view keep their own bg colour (independent of UI theme)
        bg = self._settings.value("ui/bg_colour", theme.DEFAULT_BG)
        self._apply_bg_colour(bg)
        # Cascade to child widgets
        self.ocr_panel.refresh_theme()
        self._adj_popup.refresh_theme()
        self.thumb_list.setStyleSheet(f"""
            QListWidget {{ background: {theme._active['panel_bg']}; border: none; }}
            QListWidget::item {{ border-radius: 4px; }}
            QListWidget::item:selected {{ background: {theme.ACCENT}; }}
        """)
        # Rebuild toolbar icons for the new variant
        self._rebuild_toolbar_icons()

    def _rebuild_toolbar_icons(self):
        """Reload all icons after a theme variant change.
        Every button created by _btn() and _nav_btn() stores its icon name
        as a Qt property, so we just iterate all QPushButtons in the toolbar
        and nav bar and reload from the current icons/<variant>/ folder."""
        for container in (self._toolbar, self.nav_bar):
            for btn in container.findChildren(QPushButton):
                name = btn.property("icon_name")
                if name:
                    ic = load_icon(name)
                    if not ic.isNull():
                        btn.setIcon(ic)
        # Bookmark button shows a state-dependent icon
        if hasattr(self, "tb_bookmark_btn"):
            self._update_bookmark_btn()
        # Thumbnail and OCR panel toggles show a state-dependent icon
        self._toggle_thumbnails(self.thumb_list.isVisible())
        self._toggle_ocr_panel(self.ocr_panel.isVisible())

    # ─────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────
    # Bookmarks
    # ─────────────────────────────────────────────────────────────────────────

    def _page_key(self) -> str:
        import hashlib
        h = hashlib.md5(self._current_file.encode()).hexdigest()[:12]
        return f"last_page/{h}"

    def _load_last_page(self) -> int:
        if not self._current_file:
            return 0
        return self._settings.value(self._page_key(), 0, type=int)

    def _save_last_page(self, index: int):
        if self._current_file:
            self._settings.setValue(self._page_key(), index)

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

        # Background colour
        bg = self._settings.value("ui/bg_colour", theme.DEFAULT_BG)
        self._apply_bg_colour(bg)

        # Page mode
        fit_mode = self._settings.value("ui/fit_mode", "fit_width")
        self.page_view.set_fit_mode(fit_mode)

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
        self._toast("⏳ Pre-loading OCR model…", 60000)
        self.ocr_panel.set_ocr_state("loading")
        self._warmup_worker = OCRWarmupWorker(device)
        self._warmup_worker.ready.connect(self._on_ocr_ready)
        self._warmup_worker.failed.connect(self._on_ocr_failed)
        self._warmup_worker.start()

    def _on_ocr_ready(self, dev: str):
        self.ocr_panel.set_ocr_state("ready")
        self._dismiss_toast()
        self._toast(f"✓ OCR model ready on {dev}  🐙", 4000)

    def _on_ocr_failed(self, err: str):
        self.ocr_panel.set_ocr_state("error")
        self._dismiss_toast()
        self._toast(f"⚠ OCR warmup failed: {err}", 6000)

    def _save_session_page(self, index: int):
        """Persist page position — per-file hash key + session fallback."""
        if self._settings.value("general/session_memory", True, type=bool):
            self._settings.setValue("session/last_page", index)
            self._save_last_page(index)

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


    def closeEvent(self, event):
        self._set_keep_awake(False)
        self._settings.setValue("geometry",          self.saveGeometry())
        self._settings.setValue("ui/thumb_visible",  self.thumb_list.isVisible())
        self._settings.setValue("ui/ocr_visible",    self.ocr_panel.isVisible())
        self._settings.setValue("ui/segment_on",     self.ocr_panel.seg_check.isChecked())
        self._settings.setValue("ui/page_mode",      self._page_mode)
        self._settings.setValue("ui/fit_mode",       self.page_view._fit_mode)
        shutdown_ocr()
        super().closeEvent(event)


# ─── Entry Point ──────────────────────────────────────────────────────────────


def main():
    import traceback

    # ── Subprocess dispatch (frozen builds) ──────────────────────────────
    # CUDA probe runs as the same binary with a special flag.
    # OCR runs in-process in frozen builds, so no dispatch needed for it.
    if "--cuda-probe" in sys.argv:
        from ocr import cuda_probe_main
        cuda_probe_main()
        return

    # ── Normal GUI startup ───────────────────────────────────────────────
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
        app.setStyleSheet(theme.TOOLTIP_STYLESHEET)

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
