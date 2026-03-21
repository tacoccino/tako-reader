# 🐙 Tako Reader — タコReader

A manga/comic reader built with PyQt6, focused on Japanese immersion via
drag-to-OCR text extraction powered by **manga-ocr**.

---

## Features

| Feature | Details |
|---|---|
| **File formats** | CBZ, ZIP, PDF, JPG, PNG, WebP, BMP, GIF, TIFF, AVIF, folders |
| **OCR** | Drag-select any text region → instant Japanese text extraction |
| **Reading modes** | RTL (manga default) or LTR |
| **Zoom** | Fit Width, Fit Page, or custom zoom in/out |
| **Thumbnails** | Scrollable page strip for quick navigation |
| **Dark theme** | Easy on the eyes for long reading sessions |
| **Keyboard nav** | Arrow keys, Space, Home/End, F11 fullscreen |

---

## Installation

### 1. Python 3.11+

Make sure you have Python 3.11 or newer.

### 2. Set up a virtual environment (recommended)

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 3. Install core dependencies

```bash
pip install -r requirements.txt
```

### 4. Japanese OCR (optional but recommended)

```bash
pip install manga-ocr
```

> **Note:** `manga-ocr` will download a ~400 MB transformer model on **first use**.
> After that it's cached locally. On slower machines, first OCR call takes ~10 sec.

---

## Running

```bash
# Activate venv first if not already active
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows

python tako_reader.py
# or pass a file directly:
python tako_reader.py /path/to/volume.cbz
```

---

## How to use OCR

1. Click **🔤 OCR Mode** in the toolbar (or `Ctrl+Shift+O`)
2. Your cursor changes to a crosshair
3. **Drag** a rectangle over Japanese text on the page
4. Wait ~1–3 seconds — extracted text appears in the right panel
5. **Copy** it to clipboard, then paste into Jisho, Anki, etc.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `→` / `Space` | Next page |
| `←` | Previous page |
| `Home` | First page |
| `End` | Last page |
| `W` | Fit Width |
| `F` | Fit Page |
| `Ctrl+=` | Zoom In |
| `Ctrl+-` | Zoom Out |
| `Ctrl+Shift+O` | Toggle OCR Mode |
| `F11` | Fullscreen |
| `Ctrl+O` | Open file |
| `Ctrl+Q` | Quit |

---

## Troubleshooting

### "PyMuPDF not installed" error
```bash
pip install pymupdf
```

### OCR returns garbage text
- Make sure the selection covers **only** Japanese text
- Vertical text works best when selected column by column
- Very small font sizes may need zoom-in first

### Slow first OCR
Normal — the model loads into memory on first use. Subsequent calls are fast.

### macOS: "App can't be opened"
```bash
# Run from Terminal directly:
python3 tako_reader.py
```

### Windows: DLL errors with PyQt6
Install the Visual C++ Redistributable from Microsoft's website.

---

## Recommended workflow for JP learning

1. Open manga in Tako Reader
2. Enable OCR mode
3. Select a speech bubble
4. Copy text → paste into [Jisho.org](https://jisho.org) or [Takoboto](https://takoboto.jp)
5. Add unknown words to Anki via [Yomichan/Yomitan](https://github.com/themoeway/yomitan) browser extension

---

## Dependencies

- [PyQt6](https://pypi.org/project/PyQt6/) — GUI framework
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF rendering
- [manga-ocr](https://github.com/kha-white/manga-ocr) — Japanese OCR model
- [Pillow](https://pillow.readthedocs.io/) — Image processing
- [numpy](https://numpy.org/) — Array operations
