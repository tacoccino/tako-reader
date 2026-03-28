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


def dlog(msg: str):
    """Print only when --debug flag is passed."""
    if DEBUG:
        print(f"[tako] {msg}")


def _ctrl() -> str:
    """Return 'Cmd' on macOS, 'Ctrl' everywhere else — for use in tooltips."""
    return "Cmd" if platform.system() == "Darwin" else "Ctrl"


def load_icon(name: str) -> QIcon:
    """
    Load an icon from icons/<variant>/<name>.png next to the main script.
    Falls back to an empty QIcon if the file is missing, so the app
    always runs even without the icon set.
    """
    path = Path(__file__).parent / "icons" / ICON_VARIANT / f"{name}.png"
    if path.exists():
        return QIcon(str(path))
    return QIcon()
