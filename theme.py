"""
Tako Reader — theming and style constants.
Centralises colours, stylesheets, and background presets so the main
module doesn't carry hundreds of lines of CSS strings.
"""

# ─── OCR card / text browser colours ────────────────────────────────────────

TEXT_COLOUR   = "#cdd6f4"
WORD_COLOUR   = "#93b4d4"
WORD_HOVER    = "#1e1e2e"
WORD_HOVER_BG = "#93b4d4"
BG_COLOUR     = "#1e1e2e"

# ─── OCR card widget styles ─────────────────────────────────────────────────

CARD_STYLE = """
    QWidget#OCRCard {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 6px;
    }
"""

BTN_SUBTLE = """
    QPushButton {
        background: transparent; color: #555;
        border: none; font-size: 9pt; padding: 2px 4px;
    }
    QPushButton:hover { color: #ccc; background: #2a2a3a; border-radius: 3px; }
"""

# ─── Background colour presets ──────────────────────────────────────────────

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

# ─── Application-wide tooltip stylesheet ────────────────────────────────────

TOOLTIP_STYLESHEET = """
    QToolTip {
        background: #2a2a3a;
        color: #e0e0e0;
        border: 1px solid #5a5a8a;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 9pt;
    }
"""

# ─── Dark theme (applied to QMainWindow) ────────────────────────────────────

DARK_THEME = """
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
"""
