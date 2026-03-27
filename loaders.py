"""
Tako Reader — file format loaders.
CBZ/ZIP, PDF, images, and directories.
"""

import zipfile
from pathlib import Path

from PyQt6.QtGui import QPixmap, QImage


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif"}


def load_pages_from_path(path: str) -> list[QPixmap]:
    p   = Path(path)
    ext = p.suffix.lower()
    if ext in (".cbz", ".zip"): return _load_cbz(path)
    elif ext == ".pdf":         return _load_pdf(path)
    elif ext in IMAGE_EXTS:
        px = QPixmap(path)
        return [px] if not px.isNull() else []
    elif p.is_dir():            return _load_dir(path)
    raise ValueError(f"Unsupported format: {ext}")


def _load_cbz(path: str) -> list[QPixmap]:
    pages = []
    with zipfile.ZipFile(path, "r") as zf:
        names = sorted(n for n in zf.namelist()
                       if Path(n).suffix.lower() in IMAGE_EXTS
                       and not n.startswith("__"))
        for name in names:
            img = QImage()
            img.loadFromData(zf.read(name))
            if not img.isNull():
                pages.append(QPixmap.fromImage(img))
    return pages


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


def _load_dir(path: str) -> list[QPixmap]:
    pages = []
    for f in sorted(Path(path).iterdir()):
        if f.suffix.lower() in IMAGE_EXTS:
            px = QPixmap(str(f))
            if not px.isNull():
                pages.append(px)
    return pages
