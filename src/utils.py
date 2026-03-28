"""
Tako Reader — shared utilities.
Keeps helpers that multiple modules need, avoiding circular imports.
"""

import sys
import platform
from pathlib import Path
from PyQt6.QtGui import QIcon


DEBUG = "--debug" in sys.argv

# Set by theme.apply_theme() — determines which icon subfolder to use.
# "dark"  → icons/dark/  (light-coloured icons for dark backgrounds)
# "light" → icons/light/ (dark-coloured icons for light backgrounds)
ICON_VARIANT = "dark"


# ─── Frozen-build helpers ────────────────────────────────────────────────────

def is_frozen() -> bool:
    """True if running inside a PyInstaller bundle."""
    return getattr(sys, "_MEIPASS", None) is not None


def resource_path(*parts: str) -> Path:
    """
    Resolve a path relative to the application root.

    In development this is the directory containing this file.
    In a PyInstaller bundle it's the temp _MEIPASS directory where
    bundled data files are unpacked.
    """
    if is_frozen():
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return base.joinpath(*parts)


# ─── Shared helpers ──────────────────────────────────────────────────────────

def dlog(msg: str):
    """Print only when --debug flag is passed."""
    if DEBUG:
        print(f"[tako] {msg}")


def _ctrl() -> str:
    """Return 'Cmd' on macOS, 'Ctrl' everywhere else — for use in tooltips."""
    return "Cmd" if platform.system() == "Darwin" else "Ctrl"


def load_icon(name: str) -> QIcon:
    """
    Load an icon from icons/<variant>/<n>.png.
    Works both in development and inside a PyInstaller bundle.
    Falls back to an empty QIcon if the file is missing.
    """
    path = resource_path("icons", ICON_VARIANT, f"{name}.png")
    if path.exists():
        return QIcon(str(path))
    return QIcon()
