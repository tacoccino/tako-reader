"""
Tako Reader — file format loaders.
CBZ/ZIP, CBR/RAR, CB7/7z, CBT/TAR, PDF, images, and directories.
"""

import re
import zipfile
import tarfile
from pathlib import Path

from PyQt6.QtGui import QPixmap, QImage


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}


def _nat_key(s: str):
    """Natural sort key — splits a string into text and numeric chunks
    so that 'page2' sorts before 'page10'."""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', s)]


def _is_image_name(name: str) -> bool:
    """Return True if the archive entry looks like an image (not a macOS resource fork)."""
    return (Path(name).suffix.lower() in IMAGE_EXTS
            and not name.startswith("__")
            and not name.startswith("._"))


def load_pages_from_path(path: str) -> list[QPixmap]:
    p   = Path(path)
    ext = p.suffix.lower()
    if ext in (".cbz", ".zip"):   return _load_cbz(path)
    elif ext in (".cbr", ".rar"): return _load_cbr(path)
    elif ext in (".cb7", ".7z"):  return _load_cb7(path)
    elif ext in (".cbt", ".tar"): return _load_cbt(path)
    elif ext == ".pdf":           return _load_pdf(path)
    elif ext in IMAGE_EXTS:
        px = QPixmap(path)
        return [px] if not px.isNull() else []
    elif p.is_dir():              return _load_dir(path)
    raise ValueError(f"Unsupported format: {ext}")


# ─── ZIP / CBZ ──────────────────────────────────────────────────────────────

def _load_cbz(path: str) -> list[QPixmap]:
    pages = []
    with zipfile.ZipFile(path, "r") as zf:
        names = sorted((n for n in zf.namelist() if _is_image_name(n)), key=_nat_key)
        for name in names:
            img = QImage()
            img.loadFromData(zf.read(name))
            if not img.isNull():
                pages.append(QPixmap.fromImage(img))
    return pages


# ─── RAR / CBR ──────────────────────────────────────────────────────────────

_UNRAR_HELP = (
    "CBR files require a RAR extraction tool:\n\n"
    "  macOS:   brew install unar\n"
    "  Windows: download UnRAR.exe from\n"
    "           https://www.rarlab.com/rar_add.htm\n"
    "           and add it to your PATH\n"
    "  Linux:   sudo apt install unrar"
)


def _load_cbr(path: str) -> list[QPixmap]:
    import shutil
    import tempfile
    import subprocess

    # Find whichever extraction tool is available
    tool = None
    for name in ("unrar", "unar"):
        if shutil.which(name):
            tool = name
            break

    if tool is None:
        raise ValueError(
            f"Cannot open CBR file: {Path(path).name}\n\n"
            "No RAR extraction tool found.\n\n" + _UNRAR_HELP
        )

    # Extract to a temp directory, load images from it
    with tempfile.TemporaryDirectory(prefix="tako_cbr_") as tmpdir:
        try:
            if tool == "unrar":
                subprocess.run(
                    ["unrar", "x", "-o+", "-inul", path, tmpdir + "/"],
                    check=True, capture_output=True,
                )
            elif tool == "unar":
                subprocess.run(
                    ["unar", "-force-overwrite", "-no-directory",
                     "-output-directory", tmpdir, path],
                    check=True, capture_output=True,
                )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace") if e.stderr else ""
            raise ValueError(
                f"Failed to extract CBR file: {Path(path).name}\n\n"
                f"{stderr.strip()}\n\n" + _UNRAR_HELP
            )
        except FileNotFoundError:
            raise ValueError(
                f"Cannot open CBR file: {Path(path).name}\n\n"
                f"'{tool}' was found but could not be executed.\n\n"
                + _UNRAR_HELP
            )

        # Load all extracted images (recursive — archives may have subdirs)
        pages = []
        for f in sorted(Path(tmpdir).rglob("*"), key=lambda p: _nat_key(str(p))):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS and not f.name.startswith("._"):
                px = QPixmap(str(f))
                if not px.isNull():
                    pages.append(px)
        return pages


# ─── 7z / CB7 ──────────────────────────────────────────────────────────────

def _load_cb7(path: str) -> list[QPixmap]:
    try:
        import py7zr
    except ImportError:
        raise ImportError(
            "py7zr is not installed.\n"
            "Run: pip install py7zr"
        )
    pages = []
    with py7zr.SevenZipFile(path, "r") as zf:
        all_files = zf.readall()
        names = sorted((n for n in all_files.keys() if _is_image_name(n)), key=_nat_key)
        for name in names:
            data = all_files[name].read()
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                pages.append(QPixmap.fromImage(img))
    return pages


# ─── TAR / CBT ──────────────────────────────────────────────────────────────

def _load_cbt(path: str) -> list[QPixmap]:
    pages = []
    with tarfile.open(path, "r:*") as tf:
        members = sorted(
            [m for m in tf.getmembers()
             if m.isfile() and _is_image_name(m.name)],
            key=lambda m: _nat_key(m.name)
        )
        for member in members:
            f = tf.extractfile(member)
            if f is None:
                continue
            data = f.read()
            img = QImage()
            img.loadFromData(data)
            if not img.isNull():
                pages.append(QPixmap.fromImage(img))
    return pages


# ─── PDF ────────────────────────────────────────────────────────────────────

def _load_pdf(path: str) -> list[QPixmap]:
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF not installed.\nRun: pip install pymupdf")
    pages = []
    doc   = fitz.open(path)
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height,
                     pix.stride, QImage.Format.Format_RGB888)
        pages.append(QPixmap.fromImage(img.copy()))
    doc.close()
    return pages


# ─── Directory ──────────────────────────────────────────────────────────────

def _load_dir(path: str) -> list[QPixmap]:
    pages = []
    for f in sorted(Path(path).iterdir(), key=lambda p: _nat_key(p.name)):
        if f.suffix.lower() in IMAGE_EXTS:
            px = QPixmap(str(f))
            if not px.isNull():
                pages.append(px)
    return pages
