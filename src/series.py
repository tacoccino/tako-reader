"""
Tako Reader — series / volume navigation.
Given a file path, detects sibling volumes in the same folder
and provides prev/next navigation with natural sort order.
"""

import re
from pathlib import Path


# All extensions that load_pages_from_path() can handle
SERIES_EXTS = {
    ".cbz", ".zip", ".cbr", ".rar", ".cb7", ".7z",
    ".cbt", ".tar", ".pdf",
}


def _natural_sort_key(path: Path):
    """
    Sort key that handles embedded numbers correctly.
    'vol2' < 'vol10' instead of lexicographic 'vol10' < 'vol2'.
    """
    parts = re.split(r'(\d+)', path.name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


class SeriesContext:
    """
    Scans the parent folder of a given file for sibling volumes,
    sorts them naturally, and tracks the current position.

    Usage:
        ctx = SeriesContext("/manga/Yotsuba/vol03.cbz")
        ctx.total        # 12
        ctx.current_index # 2  (0-based)
        ctx.series_name  # "Yotsuba"
        ctx.prev_path    # Path("/manga/Yotsuba/vol02.cbz") or None
        ctx.next_path    # Path("/manga/Yotsuba/vol04.cbz") or None
    """

    def __init__(self, file_path: str | Path):
        self._path = Path(file_path).resolve()
        self._volumes: list[Path] = []
        self._index: int = 0
        self._scan()

    def _scan(self):
        """Find all sibling volumes in the same folder."""
        parent = self._path.parent
        if not parent.is_dir():
            self._volumes = [self._path]
            self._index = 0
            return

        siblings = []
        for f in parent.iterdir():
            if f.is_file() and f.suffix.lower() in SERIES_EXTS:
                siblings.append(f)

        siblings.sort(key=_natural_sort_key)
        self._volumes = siblings

        # Find current file in the sorted list
        try:
            self._index = [v.resolve() for v in siblings].index(self._path)
        except ValueError:
            # Current file not in list (shouldn't happen, but be safe)
            self._volumes.append(self._path)
            self._volumes.sort(key=_natural_sort_key)
            self._index = self._volumes.index(self._path)

    @property
    def total(self) -> int:
        return len(self._volumes)

    @property
    def current_index(self) -> int:
        """0-based index of the current volume."""
        return self._index

    @property
    def series_name(self) -> str:
        """Parent folder name — used as the series name."""
        return self._path.parent.name

    @property
    def current_name(self) -> str:
        """Filename of the current volume (no extension)."""
        return self._volumes[self._index].stem

    @property
    def has_series(self) -> bool:
        """True if there are multiple volumes (i.e. series navigation is useful)."""
        return self.total > 1

    @property
    def prev_path(self) -> Path | None:
        if self._index > 0:
            return self._volumes[self._index - 1]
        return None

    @property
    def next_path(self) -> Path | None:
        if self._index < self.total - 1:
            return self._volumes[self._index + 1]
        return None

    @property
    def is_first(self) -> bool:
        return self._index == 0

    @property
    def is_last(self) -> bool:
        return self._index >= self.total - 1

    def label(self) -> str:
        """Human-readable position string, e.g. 'Vol 3 / 12'."""
        return f"Vol {self._index + 1} / {self.total}"
