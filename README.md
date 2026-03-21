# 🐙 Tako Reader — タコReader

A manga/comic reader built with PyQt6, focused on Japanese immersion via
drag-to-OCR text extraction powered by **manga-ocr**.

---

## Features

| Feature | Details |
|---|---|
| **File formats** | CBZ, ZIP, PDF, JPG, PNG, WebP, BMP, GIF, TIFF, AVIF, folders |
| **OCR** | Drag-select any text region → instant Japanese text extraction |
| **OCR device** | CPU or CUDA GPU selector in the OCR panel |
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

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `manga-ocr` is included and will download a ~400 MB transformer model on **first use**.
> It's cached locally after that. If you don't need OCR, comment it out in `requirements.txt` before installing.

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

Pass `--debug` to print startup and diagnostic info to the terminal:

```bash
python tako_reader.py --debug
```

---

## How to use OCR

1. Click **🔤 OCR Mode** in the toolbar (or `Ctrl+Shift+O`)
2. Your cursor changes to a crosshair
3. **Drag** a rectangle over Japanese text on the page
4. The first OCR call is slow (~10–15s) while the model loads — subsequent calls are fast
5. Extracted text appears in the right panel — copy and paste into Jisho, Anki, etc.

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
Normal — the model loads into a subprocess on first use. Every call after that is fast within the same session.

### macOS: "App can't be opened"
```bash
# Run from Terminal directly:
python3 tako_reader.py
```

---

## Windows: OCR / PyTorch DLL errors

OCR on Windows can fail with a `DLL initialization routine failed` error related to `c10.dll` or other PyTorch libraries. This happens because:

- The default `torch` wheel on PyPI is built with CUDA, and even the `+cpu` variant attempts to load CUDA DLLs at import time on Windows
- **RTX 50-series (Blackwell) GPUs** — CUDA driver initialisation fails for `c10.dll` on certain driver/PyTorch version combinations

**Fix: install torch CPU-only, then reinstall manga-ocr without letting it overwrite torch**

```bash
pip uninstall torch torchvision torchaudio manga-ocr -y
pip cache purge
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install manga-ocr --no-deps
pip install transformers fugashi unidic-lite jaconv Pillow
```

Verify the correct build is installed — the version string must end in `+cpu`:

```bash
python -c "import torch; print(torch.__version__)"
# Expected: 2.x.x+cpu
```

OCR will now work on CPU. The first call per session is slow (~10–15s) while the model loads; subsequent calls are fast.

### Enabling CUDA on Windows (RTX 50-series / Blackwell)

As of early 2026, stable PyTorch wheels with Blackwell (sm_120) CUDA kernel support for Windows are not yet available. You can try the nightly build:

```bash
pip uninstall torch -y
pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128
```

Once installed, reopen Tako Reader — the OCR device dropdown in the panel will show your GPU if CUDA initialises correctly. Use **OCR → Check OCR Installation** in the menu to diagnose.

### Checking what's installed

Use the built-in diagnostic via **OCR → Check OCR Installation** in the menu bar. It shows manga-ocr status, PyTorch version, and any detected CUDA devices with their compute capability.

You can also check from the terminal:

```bash
python -c "import torch; print(torch.__version__, '| CUDA:', torch.cuda.is_available())"
```

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
