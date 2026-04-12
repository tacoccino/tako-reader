"""
Tako Reader — library database.
SQLite-backed storage for per-file metadata and reading state,
keyed by path relative to the library root for portability.
"""

import json
import shutil
import sqlite3
from pathlib import Path


class LibraryDB:
    """
    Manages a SQLite database in the library root folder.
    All file paths are stored relative to the library root so
    the database is portable across machines / sync services.
    """

    DB_NAME = "library.db"
    BAK_NAME = "library.db.bak"

    def __init__(self, library_root: str):
        self._root = Path(library_root)
        self._db_path = self._root / self.DB_NAME
        self._bak_path = self._root / self.BAK_NAME
        self._conn: sqlite3.Connection | None = None
        self._open()

    # ── Connection management ─────────────────────────────────────────────

    def _open(self):
        # Backup before opening
        if self._db_path.exists():
            try:
                shutil.copy2(self._db_path, self._bak_path)
            except Exception:
                pass

        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._create_tables()

    def _create_tables(self):
        c = self._conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                rel_path    TEXT PRIMARY KEY,
                title_jp    TEXT DEFAULT '',
                title_en    TEXT DEFAULT '',
                volume      TEXT DEFAULT '',
                author      TEXT DEFAULT '',
                artist      TEXT DEFAULT '',
                circle      TEXT DEFAULT '',
                publisher   TEXT DEFAULT '',
                year        TEXT DEFAULT '',
                language    TEXT DEFAULT '',
                tags        TEXT DEFAULT '',
                notes       TEXT DEFAULT '',
                rating      TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS file_state (
                rel_path    TEXT PRIMARY KEY,
                last_page       INTEGER DEFAULT 0,
                bookmarks       TEXT DEFAULT '[]',
                reading_mode    TEXT DEFAULT '',
                page_offset     INTEGER DEFAULT 0,
                rotation        INTEGER DEFAULT 0,
                adjustments     TEXT DEFAULT '{}',
                ocr_results     TEXT DEFAULT '[]'
            )
        """)
        c.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def root(self) -> Path:
        return self._root

    # ── Path helpers ──────────────────────────────────────────────────────

    def rel_path(self, abs_path: str) -> str:
        """Convert an absolute file path to a path relative to the library root."""
        try:
            return str(Path(abs_path).resolve().relative_to(self._root.resolve()))
        except ValueError:
            return ""

    def abs_path(self, rel: str) -> str:
        """Convert a relative path back to absolute."""
        return str(self._root / rel)

    def is_in_library(self, abs_path: str) -> bool:
        """Check if a file is inside this library's root folder."""
        try:
            Path(abs_path).resolve().relative_to(self._root.resolve())
            return True
        except ValueError:
            return False

    # ── Metadata ──────────────────────────────────────────────────────────

    _META_FIELDS = [
        "title_jp", "title_en", "volume", "author", "artist",
        "circle", "publisher", "year", "language", "tags",
        "notes", "rating",
    ]

    def get_metadata(self, abs_path: str) -> dict:
        """Return metadata dict for a file. Empty dict if not found."""
        rp = self.rel_path(abs_path)
        if not rp:
            return {}
        row = self._conn.execute(
            "SELECT * FROM metadata WHERE rel_path = ?", (rp,)
        ).fetchone()
        if not row:
            return {}
        return {k: row[k] for k in self._META_FIELDS if row[k]}

    def set_metadata(self, abs_path: str, data: dict):
        """Save metadata dict for a file (upsert)."""
        rp = self.rel_path(abs_path)
        if not rp:
            return
        values = {k: data.get(k, "") for k in self._META_FIELDS}
        values["rel_path"] = rp
        cols = ", ".join(values.keys())
        placeholders = ", ".join(["?"] * len(values))
        updates = ", ".join(
            f"{k} = excluded.{k}" for k in self._META_FIELDS
        )
        self._conn.execute(
            f"INSERT INTO metadata ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(rel_path) DO UPDATE SET {updates}",
            list(values.values()),
        )
        self._conn.commit()

    def get_all_metadata(self) -> dict[str, dict]:
        """Return metadata for all files. Keyed by rel_path."""
        rows = self._conn.execute("SELECT * FROM metadata").fetchall()
        result = {}
        for row in rows:
            d = {k: row[k] for k in self._META_FIELDS if row[k]}
            if d:
                result[row["rel_path"]] = d
        return result

    # ── File state ────────────────────────────────────────────────────────

    _STATE_FIELDS_SIMPLE = [
        "last_page", "reading_mode", "page_offset", "rotation",
    ]
    _STATE_FIELDS_JSON = [
        "bookmarks", "adjustments", "ocr_results",
    ]

    def get_file_state(self, abs_path: str) -> dict:
        """Return file state dict. Empty dict if not found."""
        rp = self.rel_path(abs_path)
        if not rp:
            return {}
        row = self._conn.execute(
            "SELECT * FROM file_state WHERE rel_path = ?", (rp,)
        ).fetchone()
        if not row:
            return {}
        state = {}
        for k in self._STATE_FIELDS_SIMPLE:
            val = row[k]
            if val is not None and val != "" and val != 0:
                state[k] = val
        for k in self._STATE_FIELDS_JSON:
            raw = row[k]
            if raw and raw not in ("[]", "{}"):
                try:
                    state[k] = json.loads(raw)
                except Exception:
                    pass
        return state

    def set_file_state(self, abs_path: str, **kwargs):
        """Save individual file state fields (upsert).
        Only the provided kwargs are updated; others are left unchanged.

        Usage:
            db.set_file_state(path, last_page=5, reading_mode="rtl")
        """
        rp = self.rel_path(abs_path)
        if not rp:
            return

        # Serialize JSON fields
        values = {}
        for k, v in kwargs.items():
            if k in self._STATE_FIELDS_JSON:
                values[k] = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            else:
                values[k] = v

        # Check if row exists
        existing = self._conn.execute(
            "SELECT rel_path FROM file_state WHERE rel_path = ?", (rp,)
        ).fetchone()

        if existing:
            sets = ", ".join(f"{k} = ?" for k in values)
            self._conn.execute(
                f"UPDATE file_state SET {sets} WHERE rel_path = ?",
                list(values.values()) + [rp],
            )
        else:
            values["rel_path"] = rp
            cols = ", ".join(values.keys())
            placeholders = ", ".join(["?"] * len(values))
            self._conn.execute(
                f"INSERT INTO file_state ({cols}) VALUES ({placeholders})",
                list(values.values()),
            )
        self._conn.commit()

    def get_all_file_states(self) -> dict[str, dict]:
        """Return file state for all files. Keyed by rel_path."""
        rows = self._conn.execute("SELECT * FROM file_state").fetchall()
        result = {}
        for row in rows:
            state = {}
            for k in self._STATE_FIELDS_SIMPLE:
                val = row[k]
                if val is not None and val != "" and val != 0:
                    state[k] = val
            for k in self._STATE_FIELDS_JSON:
                raw = row[k]
                if raw and raw not in ("[]", "{}"):
                    try:
                        state[k] = json.loads(raw)
                    except Exception:
                        pass
            if state:
                result[row["rel_path"]] = state
        return result
