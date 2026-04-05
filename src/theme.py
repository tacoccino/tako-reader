"""
Tako Reader — theming engine.
Defines four built-in themes and an accent colour system.
All colours and generated stylesheets live here so the rest of the
codebase just reads module-level constants.

Call apply_theme(theme_id, accent) once at startup and again whenever
the user changes theme or accent.  It updates every module-level
constant in-place so existing `theme.TEXT_COLOUR` references stay valid.
"""

from __future__ import annotations

# ─── Theme definitions ──────────────────────────────────────────────────────

THEMES: dict[str, dict] = {
    "dark": {
        "name":       "Dark",
        "icon_variant": "dark",
        "window_bg":  "#282828",
        "toolbar_bg": "#313131",
        "panel_bg":   "#282828",
        "card_bg":    "#252537",
        "input_bg":   "#2a2a2a",
        "nav_bg":     "#262626",
        "popup_bg":   "#252535",
        "menu_bg":    "#313131",
        "hover_bg":   "#2e2e2e",
        "border":     "#2a2a2a",
        "border_light": "#313244",
        "popup_border": "#3a3a5a",
        "text":       "#e0e0e0",
        "text_secondary": "#aaa",
        "text_muted": "#666",
        "scrollbar_handle": "#3a3a3a",
        "tooltip_bg": "#2a2a3a",
        "tooltip_border": "#5a5a8a",
        "ocr_text":   "#cdd6f4",
        "ocr_word":   "#93b4d4",
        "ocr_word_hover_fg": "#1e1e2e",
        "ocr_word_hover_bg": "#93b4d4",
        "separator":  "#404040",
        "separator_bg": "#313131",
    },
    "light": {
        "name":       "Light",
        "icon_variant": "light",
        "window_bg":  "#e4e4e4",
        "toolbar_bg": "#dcdcdc",
        "panel_bg":   "#d6d6d6",
        "card_bg":    "#efefef",
        "input_bg":   "#f8f8f8",
        "nav_bg":     "#d2d2d2",
        "popup_bg":   "#f0f0f0",
        "menu_bg":    "#ececec",
        "hover_bg":   "#d0d0d0",
        "border":     "#bbb",
        "border_light": "#ccc",
        "popup_border": "#bbb",
        "text":       "#1a1a1a",
        "text_secondary": "#555",
        "text_muted": "#888",
        "scrollbar_handle": "#aaa",
        "tooltip_bg": "#f5f5f5",
        "tooltip_border": "#bbb",
        "ocr_text":   "#1a1a1a",
        "ocr_word":   "#1a5599",
        "ocr_word_hover_fg": "#ffffff",
        "ocr_word_hover_bg": "#1a5599",
        "separator":  "#c0c0c0",
        "separator_bg": "#dcdcdc",
    },
    "oled_black": {
        "name":       "OLED Black",
        "icon_variant": "dark",
        "window_bg":  "#000000",
        "toolbar_bg": "#0a0a0a",
        "panel_bg":   "#050505",
        "card_bg":    "#111118",
        "input_bg":   "#1a1a1a",
        "nav_bg":     "#080808",
        "popup_bg":   "#151520",
        "menu_bg":    "#111111",
        "hover_bg":   "#1a1a1a",
        "border":     "#1a1a1a",
        "border_light": "#222233",
        "popup_border": "#333350",
        "text":       "#e0e0e0",
        "text_secondary": "#999",
        "text_muted": "#555",
        "scrollbar_handle": "#333",
        "tooltip_bg": "#1a1a2a",
        "tooltip_border": "#3a3a5a",
        "ocr_text":   "#cdd6f4",
        "ocr_word":   "#93b4d4",
        "ocr_word_hover_fg": "#0a0a0a",
        "ocr_word_hover_bg": "#93b4d4",
        "separator":  "#222",
        "separator_bg": "#0a0a0a",
    },
    "bright": {
        "name":       "Bright",
        "icon_variant": "light",
        "window_bg":  "#ffffff",
        "toolbar_bg": "#f8f8f8",
        "panel_bg":   "#f2f2f2",
        "card_bg":    "#ffffff",
        "input_bg":   "#ffffff",
        "nav_bg":     "#f5f5f5",
        "popup_bg":   "#ffffff",
        "menu_bg":    "#ffffff",
        "hover_bg":   "#ebebeb",
        "border":     "#ddd",
        "border_light": "#e0e0e0",
        "popup_border": "#ccc",
        "text":       "#111111",
        "text_secondary": "#555",
        "text_muted": "#999",
        "scrollbar_handle": "#bbb",
        "tooltip_bg": "#ffffff",
        "tooltip_border": "#ccc",
        "ocr_text":   "#111111",
        "ocr_word":   "#1a5599",
        "ocr_word_hover_fg": "#ffffff",
        "ocr_word_hover_bg": "#1a5599",
        "separator":  "#ddd",
        "separator_bg": "#f8f8f8",
    },
}

# ─── Accent presets ─────────────────────────────────────────────────────────

ACCENT_PRESETS = [
    ("Blue",   "#3584e4"),
    ("Teal",   "#2ec4b6"),
    ("Green",  "#2ecc71"),
    ("Orange", "#e67e22"),
    ("Pink",   "#e84393"),
    ("Red",    "#e74c3c"),
]

DEFAULT_ACCENT = "#3584e4"

# ─── Active state — read by the rest of the app via module-level names ──────

_active_id: str = "dark"
_active: dict = THEMES["dark"]
ACCENT: str = DEFAULT_ACCENT

# OCR card / text browser colours (backward-compatible names)
TEXT_COLOUR   = _active["ocr_text"]
WORD_COLOUR   = _active["ocr_word"]
WORD_HOVER    = _active["ocr_word_hover_fg"]
WORD_HOVER_BG = _active["ocr_word_hover_bg"]
BG_COLOUR     = _active["card_bg"]

# ─── Background colour presets (page area — independent of UI theme) ────────

DEFAULT_BG = "#1a1a1a"

BG_PRESETS = [
    ("Dark (default)", "#1a1a1a"),
    ("Black",          "#000000"),
    ("Dark Grey",      "#2d2d2d"),
    ("Warm Grey",      "#3a3530"),
    ("White",          "#ffffff"),
    ("Off-white",      "#f5f0e8"),
    ("Sepia",          "#f4ecd8"),
    ("Paper",          "#e8e0d0"),
]

# ─── Generated stylesheets — rebuilt by apply_theme() ───────────────────────

CARD_STYLE = ""
BTN_SUBTLE = ""
APP_STYLESHEET = ""
TOOLTIP_STYLESHEET = ""
POPUP_STYLESHEET = ""
SETTINGS_STYLESHEET = ""
THUMBNAIL_SCROLLBAR_STYLESHEET = ""


def _t(key: str) -> str:
    """Shorthand to read from the active theme dict."""
    return _active[key]


def _rebuild_styles():
    """Rebuild all module-level stylesheet strings from active theme + accent."""
    global CARD_STYLE, BTN_SUBTLE, APP_STYLESHEET, TOOLTIP_STYLESHEET
    global POPUP_STYLESHEET, SETTINGS_STYLESHEET, THUMBNAIL_SCROLLBAR_STYLESHEET
    a = ACCENT

    CARD_STYLE = f"""
        QWidget#OCRCard {{
            background: {_t('card_bg')};
            border: 1px solid {_t('border_light')};
            border-radius: 6px;
        }}
        QWidget#OCRCard QPushButton {{
            background: transparent; color: {_t('text_muted')};
            border: none; font-size: 9pt; padding: 2px 4px;
        }}
        QWidget#OCRCard QPushButton:hover {{
            color: {_t('text')}; background: {_t('hover_bg')}; border-radius: 3px;
        }}
    """

    BTN_SUBTLE = f"""
        QPushButton {{
            background: transparent; color: {_t('text_muted')};
            border: none; font-size: 9pt; padding: 2px 4px;
        }}
        QPushButton:hover {{ color: {_t('text')}; background: {_t('hover_bg')}; border-radius: 3px; }}
    """

    TOOLTIP_STYLESHEET = f"""
        QToolTip {{
            background: {_t('tooltip_bg')};
            color: {_t('text')};
            border: 1px solid {_t('tooltip_border')};
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 9pt;
        }}
    """

    APP_STYLESHEET = f"""
        QMainWindow, QWidget {{ background: {_t('window_bg')}; color: {_t('text')}; }}

        QMenuBar {{ background: {_t('toolbar_bg')}; color: {_t('text')};
                    border-bottom: 1px solid {_t('border')}; }}
        QMenuBar::item:selected {{ background: {a}; color: #fff; }}

        QMenu {{ background: {_t('menu_bg')}; color: {_t('text')};
                 border: 1px solid {_t('border')}; }}
        QMenu::item:selected {{ background: {a}; color: #fff; }}
        QMenu::separator {{ background: {_t('border')}; height: 1px; margin: 2px 8px; }}

        #ToolBar {{ background: {_t('toolbar_bg')}; border-bottom: 1px solid {_t('border')}; }}
        #ToolBar QPushButton {{
            background: transparent; color: {_t('text_secondary')};
            border: none; border-radius: 4px;
            padding: 4px 8px; font-size: 10pt;
        }}
        #ToolBar QPushButton:hover {{ background: {_t('hover_bg')}; color: {_t('text')}; }}
        #ToolBar QPushButton:checked {{ background: {a}; color: #fff; }}
        #ToolBar QFrame {{ 
            background: {_t('separator_bg')}; color: {_t('separator')}; 
            border-bottom: 1px solid {_t('border')};
        }}

        #NavBar {{ background: {_t('nav_bg')}; border-top: 1px solid {_t('border')}; }}
        #NavBar QPushButton {{
            background: {_t('nav_bg')}; color: {_t('text')};
            border-radius: 6px; padding: 0 10px; font-size: 10pt;
        }}
        #NavBar QPushButton:hover {{ background: {a}; color: #fff; }}
        #NavBar QPushButton:disabled {{ color: {_t('text_muted')}; }}
        #NavBar QLabel {{ color: {_t('text_secondary')}; }}
        #NavBar QLineEdit {{
            background: {_t('input_bg')}; color: {_t('text')};
            border: 1px solid {a}; border-radius: 4px;
            font-size: 10pt; padding: 2px;
        }}

        QStatusBar {{ background: {_t('toolbar_bg')}; color: {_t('text_muted')};
                      font-size: 9pt; border-top: 1px solid {_t('border')}; }}

        QScrollBar:vertical   {{ background: {_t('window_bg')}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: {_t('scrollbar_handle')}; border-radius: 5px;
                                       min-height: 30px; }}
        QScrollBar:horizontal {{ background: {_t('window_bg')}; height: 10px; }}
        QScrollBar::handle:horizontal {{ background: {_t('scrollbar_handle')}; border-radius: 5px; }}
    """

    POPUP_STYLESHEET = f"""
        QWidget {{
            background: {_t('popup_bg')};
            color: {_t('text')};
            border: 1px solid {_t('popup_border')};
            border-radius: 8px;
        }}
        QLabel {{ border: none; background: transparent; }}
        QPushButton {{
            background: {_t('input_bg')}; color: {_t('text_secondary')};
            border: 1px solid {_t('border_light')}; border-radius: 5px;
            padding: 4px 10px; font-size: 9pt;
        }}
        QPushButton:hover {{ background: {a}; color: #fff; border-color: {a}; }}
        QScrollBar:vertical {{ background: {_t('popup_bg')}; width: 6px; }}
        QScrollBar::handle:vertical {{ background: {_t('scrollbar_handle')}; border-radius: 3px; }}
        QLineEdit {{
            background: {_t('card_bg')}; color: {_t('text')};
            border: 1px solid {_t('popup_border')}; border-radius: 4px;
            padding: 2px 6px; font-size: 9pt;
        }}
    """

    THUMBNAIL_SCROLLBAR_STYLESHEET = f"""
        QListWidget {{ background: {_t('panel_bg')}; border: none; }}
        QListWidget::item {{ border-radius: 4px; }}
        QScrollBar:vertical {{ 
            background: {_t('panel_bg')}; 
            width: 10px; margin: 0px; 
            border: none; padding: 1px; 
        }}
        QScrollBar::handle:vertical {{ border-radius: 4px; min-height: 20px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar:left-arrow:vertical, QScrollBar::right-arrow:vertical {{ 
            border: 2px solid grey;
            width: 3px;
            height: 3px;
            background: white;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{background: none;}}

    """

    SETTINGS_STYLESHEET = f"""
        QDialog {{ background: {_t('window_bg')}; color: {_t('text')}; }}
        QLabel  {{ color: {_t('text')}; }}
        QTabWidget::pane {{
            border: 1px solid {_t('border')};
            background: {_t('window_bg')};
        }}
        QTabBar::tab {{
            min-width: 80px;
            background: {_t('toolbar_bg')}; color: {_t('text_muted')};
            padding: 8px 15px; font-size: 9pt;
            border: 1px solid {_t('border')};
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{ background: {_t('window_bg')}; color: {_t('text')}; }}
        QTabBar::tab:hover:!selected {{ background: {_t('hover_bg')}; color: {_t('text_secondary')}; }}
        QGroupBox {{
            color: {_t('text_secondary')}; font-size: 9pt; font-weight: bold;
            border: 1px solid {_t('border')}; border-radius: 6px;
            margin-top: 8px; padding-top: 12px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; subcontrol-position: top left;
            left: 10px; padding: 0 4px;
        }}
        QComboBox {{
            background: {_t('input_bg')}; color: {_t('text')};
            border: 1px solid {_t('border_light')}; border-radius: 4px;
            padding: 4px 8px; font-size: 10pt;
            combobox-popup: 0;
        }}
        QComboBox::drop-down {{
            border-left: 1px solid {_t('border_light')};
            width: 24px;
            border-top-right-radius: 4px;
            border-bottom-right-radius: 4px;
        }}
        QComboBox::down-arrow {{
            image: none; width: 0; height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {_t('text_muted')};
        }}
        QComboBox::drop-down:hover {{ background: {_t('hover_bg')}; }}
        QComboBox::down-arrow:hover {{ border-top-color: {_t('text')}; }}
        QComboBox QAbstractItemView, QComboBox QListView {{
            background-color: {_t('input_bg')}; color: {_t('text')};
            selection-background-color: {a}; selection-color: #fff;
            border: 1px solid {_t('border')};
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            background-color: {_t('input_bg')}; color: {_t('text')};
            padding: 4px 8px;
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: {a}; color: #fff;
        }}
        QComboBox QFrame {{
            background-color: {_t('input_bg')};
            border: 1px solid {_t('border')};
        }}
        QCheckBox::indicator {{
            width: 16px; height: 16px;
            border: 1px solid {_t('border_light')}; border-radius: 3px;
            background: {_t('input_bg')};
        }}
        QCheckBox::indicator:checked {{
            background: {a}; border-color: {a};
        }}
        QSpinBox {{
            background: {_t('input_bg')}; color: {_t('text')};
            border: 1px solid {_t('border_light')}; border-radius: 4px;
            padding: 2px 6px; font-size: 9pt;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 16px; background: {_t('hover_bg')}; border: none;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: {a};
        }}
        QLineEdit {{
            background: {_t('input_bg')}; color: {_t('text')};
            border: 1px solid {_t('border_light')}; border-radius: 4px;
            padding: 4px 6px; font-size: 10pt;
        }}
        QPushButton {{
            background: {_t('input_bg')}; color: {_t('text')};
            border: 1px solid {_t('border_light')}; border-radius: 6px;
            padding: 6px 20px; font-size: 10pt; min-width: 80px;
        }}
        QPushButton:hover {{ background: {a}; color: #fff; border-color: {a}; }}
    """


# ─── Public API ─────────────────────────────────────────────────────────────

def current_id() -> str:
    """Return the active theme ID."""
    return _active_id


def current_theme() -> dict:
    """Return the full active theme dict."""
    return dict(_active)


def apply_theme(theme_id: str = "dark", accent: str = ""):
    """
    Activate a theme and optional accent colour.
    Updates every module-level constant so existing references stay valid.
    """
    global _active_id, _active, ACCENT
    global TEXT_COLOUR, WORD_COLOUR, WORD_HOVER, WORD_HOVER_BG, BG_COLOUR

    if theme_id not in THEMES:
        theme_id = "dark"
    _active_id = theme_id
    _active    = THEMES[theme_id]

    if accent:
        ACCENT = accent

    # Update icon variant in utils (import here to avoid circular dep at module load)
    import utils
    utils.ICON_VARIANT = _active["icon_variant"]

    # Update backward-compatible OCR colour names
    TEXT_COLOUR   = _active["ocr_text"]
    WORD_COLOUR   = _active["ocr_word"]
    WORD_HOVER    = _active["ocr_word_hover_fg"]
    WORD_HOVER_BG = _active["ocr_word_hover_bg"]
    BG_COLOUR     = _active["card_bg"]

    # Rebuild all stylesheet strings
    _rebuild_styles()


# ─── Helper for widgets that need inline styles with theme tokens ───────────

def ocr_browser_stylesheet() -> str:
    return f"""
        QTextBrowser {{
            background: {_t('card_bg')};
            color: {_t('ocr_text')};
            border: none;
            border-radius: 6px;
            padding: 6px 6px 24px 6px;
            font-size: 18px;
        }}
    """


def segment_btn_stylesheet() -> str:
    a = ACCENT
    return f"""
        QPushButton {{
            background: transparent; color: {_t('text_muted')};
            border: 1px solid {_t('border_light')}; border-radius: 4px;
            padding: 2px 8px; font-size: 9pt;
        }}
        QPushButton:hover   {{ color: {_t('text_secondary')}; border-color: {_t('text_muted')}; }}
        QPushButton:checked {{ background: {a}; color: #fff; border-color: {a}; }}
    """


def popup_header_stylesheet() -> str:
    return (
        f"background: {_t('card_bg')}; border-bottom: 1px solid {_t('popup_border')};"
        f" border-top-left-radius: 8px; border-top-right-radius: 8px;"
    )


def slider_popup_stylesheet() -> str:
    a = ACCENT
    return f"""
        QSlider::groove:horizontal {{ height: 4px; background: {_t('popup_border')}; border-radius: 2px; }}
        QSlider::handle:horizontal {{
            background: {a}; border-radius: 7px;
            width: 14px; margin: -5px 0;
        }}
        QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
    """


def bg_swatch_stylesheet(colour: str) -> str:
    return (
        f"QPushButton {{ background: {colour}; border: 1px solid {_t('border_light')};"
        f"border-radius: 4px; min-width: 18px; max-width: 18px;"
        f"min-height: 18px; max-height: 18px; }}"
        f"QPushButton:hover {{ border-color: {_t('text_secondary')}; }}"
    )


def toast_stylesheet() -> str:
    return f"""
        QLabel {{
            background: rgba(0, 0, 0, 180);
            color: #fff;
            border-radius: 8px;
            padding: 8px 20px;
            font-size: 10pt;
        }}
    """


# ─── Initialise defaults ────────────────────────────────────────────────────
_rebuild_styles()
