"""
Tako Reader — library database.
SQLite-backed storage for per-file metadata and reading state,
keyed by path relative to the library root for portability.
"""

import json
import shutil
import sqlite3
import unicodedata
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                name    TEXT PRIMARY KEY,
                color   TEXT DEFAULT 'gray'
            )
        """)
        c.commit()
        self._normalize_paths()
        self.collect_tags_from_metadata()

    def _normalize_paths(self):
        """Fix backslash paths and Unicode normalization in existing entries."""
        for table in ("metadata", "file_state"):
            rows = self._conn.execute(
                f"SELECT rel_path FROM {table}"
            ).fetchall()
            for row in rows:
                old_path = row["rel_path"]
                new_path = unicodedata.normalize(
                    "NFC", old_path.replace("\\", "/")
                )
                if old_path == new_path:
                    continue
                # Check if normalized version already exists
                existing = self._conn.execute(
                    f"SELECT rel_path FROM {table} WHERE rel_path = ?",
                    (new_path,),
                ).fetchone()
                if existing:
                    # Delete the non-normalized duplicate
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE rel_path = ?",
                        (old_path,),
                    )
                else:
                    # Rename to normalized form
                    self._conn.execute(
                        f"UPDATE {table} SET rel_path = ? WHERE rel_path = ?",
                        (new_path, old_path),
                    )
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def root(self) -> Path:
        return self._root

    # ── Path helpers ──────────────────────────────────────────────────────

    def rel_path(self, abs_path: str) -> str:
        """Convert an absolute file path to a path relative to the library root.
        Always uses forward slashes and NFC Unicode for cross-platform portability."""
        try:
            rel = Path(abs_path).resolve().relative_to(self._root.resolve())
            return unicodedata.normalize("NFC", rel.as_posix())
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
        # Try forward slashes first, then backslashes for legacy entries
        for try_path in (rp, rp.replace("/", "\\")):
            row = self._conn.execute(
                "SELECT * FROM metadata WHERE rel_path = ?", (try_path,)
            ).fetchone()
            if row:
                return {k: row[k] for k in self._META_FIELDS if row[k]}
        return {}

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
        """Return metadata for all files. Keyed by rel_path (NFC, forward slashes)."""
        rows = self._conn.execute("SELECT * FROM metadata").fetchall()
        result = {}
        for row in rows:
            d = {k: row[k] for k in self._META_FIELDS if row[k]}
            if d:
                rp = unicodedata.normalize("NFC", row["rel_path"].replace("\\", "/"))
                result[rp] = d
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
        row = None
        for try_path in (rp, rp.replace("/", "\\")):
            row = self._conn.execute(
                "SELECT * FROM file_state WHERE rel_path = ?", (try_path,)
            ).fetchone()
            if row:
                break
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

        # Check if row exists (try both slash styles)
        existing = None
        for try_path in (rp, rp.replace("/", "\\")):
            existing = self._conn.execute(
                "SELECT rel_path FROM file_state WHERE rel_path = ?", (try_path,)
            ).fetchone()
            if existing:
                # If found with backslashes, update to use forward slashes
                if try_path != rp:
                    self._conn.execute(
                        "UPDATE file_state SET rel_path = ? WHERE rel_path = ?",
                        (rp, try_path),
                    )
                break

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
                rp = unicodedata.normalize("NFC", row["rel_path"].replace("\\", "/"))
                result[rp] = state
        return result

    # ── Tag registry ──────────────────────────────────────────────────────

    def get_all_tags(self) -> dict[str, str]:
        """Return all registered tags as {name: color}."""
        rows = self._conn.execute("SELECT name, color FROM tags").fetchall()
        return {row["name"]: row["color"] for row in rows}

    def ensure_tags(self, tag_names: list[str]):
        """Register tags if they don't already exist (default color: gray)."""
        for name in tag_names:
            name = name.strip()
            if not name:
                continue
            self._conn.execute(
                "INSERT OR IGNORE INTO tags (name, color) VALUES (?, 'gray')",
                (name,),
            )
        self._conn.commit()

    def set_tag_color(self, name: str, color: str):
        """Update a tag's color."""
        self._conn.execute(
            "UPDATE tags SET color = ? WHERE name = ?", (color, name)
        )
        self._conn.commit()

    def rename_tag(self, old_name: str, new_name: str):
        """Rename a tag across all files and the tag registry."""
        self._conn.execute(
            "UPDATE tags SET name = ? WHERE name = ?", (new_name, old_name)
        )
        rows = self._conn.execute(
            "SELECT rel_path, tags FROM metadata WHERE tags LIKE ?",
            (f"%{old_name}%",),
        ).fetchall()
        for row in rows:
            tag_list = [t.strip() for t in row["tags"].split(",") if t.strip()]
            tag_list = [new_name if t == old_name else t for t in tag_list]
            self._conn.execute(
                "UPDATE metadata SET tags = ? WHERE rel_path = ?",
                (", ".join(tag_list), row["rel_path"]),
            )
        self._conn.commit()

    def delete_tag(self, name: str):
        """Remove a tag from the registry and all files."""
        self._conn.execute("DELETE FROM tags WHERE name = ?", (name,))
        rows = self._conn.execute(
            "SELECT rel_path, tags FROM metadata WHERE tags LIKE ?",
            (f"%{name}%",),
        ).fetchall()
        for row in rows:
            tag_list = [t.strip() for t in row["tags"].split(",") if t.strip()]
            tag_list = [t for t in tag_list if t != name]
            self._conn.execute(
                "UPDATE metadata SET tags = ? WHERE rel_path = ?",
                (", ".join(tag_list), row["rel_path"]),
            )
        self._conn.commit()

    def collect_tags_from_metadata(self):
        """Scan all metadata tag strings and ensure each tag is in the registry."""
        rows = self._conn.execute(
            "SELECT tags FROM metadata WHERE tags != ''"
        ).fetchall()
        all_tags = set()
        for row in rows:
            for t in row["tags"].split(","):
                t = t.strip()
                if t:
                    all_tags.add(t)
        if all_tags:
            self.ensure_tags(list(all_tags))
