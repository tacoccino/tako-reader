# 🐙 Tako Reader — タコReader

A manga reader built for Japanese immersion. Drag-select any text region for instant OCR, look up words in an offline dictionary, and add vocabulary cards to Anki — all without leaving the reader.

**Source:** [github.com/tacoccino/tako-reader](https://github.com/tacoccino/tako-reader)

![Screenshot 2026-03-28 at 11 35 32 AM Large](https://github.com/user-attachments/assets/bdfa0f9f-9d24-4ba3-a09d-c168f818180c)

---

## Features

### Reader
- **Formats:** CBZ, ZIP, PDF, images (JPG, PNG, WebP, BMP, GIF, TIFF, AVIF), folders
- **Page modes:** Single page or double-page spread (RTL and LTR)
- **Zoom:** Fit Width, Fit Page, or custom zoom — fit mode is remembered across sessions
- **Fullscreen:** F11 / toolbar button — all UI hides, arrow keys still navigate
- **Thumbnails:** Scrollable page strip for quick navigation
- **Bookmarks:** Bookmark any page, name it, jump back to it via popup or menu
- **Reading history:** File → Open Recent shows your last 10 opened files
- **Session memory:** Reopens your last file at the last page (configurable)
- **Background colour:** Presets (dark, sepia, white, etc.) or custom colour picker
- **Jump to page:** Click the page indicator in the nav bar, type a number, press Enter
- **Image adjustments:** Brightness, contrast, saturation, sharpness, and warmth — per-file, persisted across sessions
- **Rotation:** Rotate pages left/right — persisted per file
- **Page preloading:** Upcoming pages load in the background for instant page turns
- **Keep screen awake:** Prevents the display from sleeping while reading (macOS and Windows)
- **Drag and drop:** Drop a file or folder onto the window to open it

### Themes
- **Four built-in themes:** Dark (default), Light, OLED Black, Bright
- **Accent colour:** Six presets (blue, teal, green, orange, pink, red) or custom picker — controls button highlights, selections, and focus rings
- Icons automatically switch between light and dark variants to match the active theme
- Theme and accent persist across sessions

### OCR
- Drag a rectangle over any Japanese text to extract it
- Each selection creates its own **card** in the OCR panel
- Cards can be **merged** (for split speech bubbles) or dismissed individually
- **Segmentation mode:** Tokenises text into clickable words using fugashi
- Hover highlighting shows which word you're about to click
- OCR model pre-loading at startup (optional, Settings → OCR)
- CPU and CUDA device support

### Dictionary
- **Offline lookup** powered by JMdict / KANJIDIC2 via jamdict
- Floating popup shows: word, reading, numbered definitions, kanji breakdown with on/kun readings
- Accessible by clicking any segmented word, right-click context menu, or `Ctrl+D`
- External links to Jisho and Takoboto for each word

### Anki Integration
- Connects to a running Anki instance via AnkiConnect
- Per-entry **+ Anki** button in the dictionary popup
- `Ctrl+click` to edit card fields before adding
- Fields: Word, Reading, Furigana (ruby HTML), Definition, Sentence, Image
- **Image capture:** Select a region from the manga page to attach to Anki cards
- Flexible field mapping — maps Tako Reader data to any note type field
- Deck and note type selection, all configured in Settings → Anki
- Non-blocking — adding cards happens in the background

### Customisation
- **Keyboard shortcuts:** All shortcuts are remappable in Settings → Shortcuts
- **Background colour:** Independent of UI theme — choose any colour for the reading area

---

## Quick Start

### Download and run (recommended)

The easiest way to get started — no Python knowledge required.

1. Download or clone the repository
2. **Windows:** Double-click `Install Tako Reader.bat`
   **macOS:** Double-click `Install Tako Reader.command` (right-click → Open if blocked by Gatekeeper)
3. Wait for installation to complete (~3-5 minutes, downloads ~400 MB)
4. **Windows:** Double-click `Tako Reader.bat`
   **macOS:** Double-click `Tako Reader.command`

The installer creates a local Python environment inside the project folder (`.venv`). No system-wide changes are made. Run the installer again at any time to update packages.

> **Note:** On macOS, you may need to run `chmod +x *.command` in Terminal first if the `.command` files aren't executable after unzipping.

---

## Manual Installation

If you prefer to manage your own Python environment.

### Requirements
- Python 3.11 or newer
- Anki (optional, for card creation)
- AnkiConnect add-on (optional, for card creation)

### 1. Clone the repository

```bash
git clone https://github.com/tacoccino/tako-reader.git
cd tako-reader
```

### 2. Create a virtual environment

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

> **Note:** `manga-ocr` downloads a ~400 MB transformer model on **first use**. It's cached locally after that.

### 4. Install the dictionary database

**macOS / Linux:**
```bash
pip install jamdict jamdict-data
```

**Windows** (standard `jamdict-data` fails due to a file lock bug — use the fix variant):
```bash
pip install jamdict jamdict-data-fix
```

Verify the database installed correctly:
```bash
python -m jamdict lookup 食べる
```
You should see entries with readings and definitions.

---

## Running

```bash
# Activate venv first if not already active
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows

python src/tako_reader.py

# Or pass a file directly:
python src/tako_reader.py /path/to/volume.cbz

# Debug mode (prints startup and diagnostic info):
python src/tako_reader.py --debug
```

Or use the launcher scripts at the project root (`Tako Reader.bat` / `Tako Reader.command`).

---

## Keyboard Shortcuts

All shortcuts can be customised in **Settings → Shortcuts**.

| Key | Action |
|---|---|
| `→` / `Space` / `N` | Next page |
| `←` / `B` / `P` | Previous page |
| `Home` | First page |
| `End` | Last page |
| `W` | Fit Width |
| `F` | Fit Page |
| `Ctrl+=` | Zoom In |
| `Ctrl+-` | Zoom Out |
| `[` | Rotate Left |
| `]` | Rotate Right |
| `F11` / `Esc` | Toggle fullscreen |
| `Ctrl+G` | Jump to page |
| `Ctrl+O` | Open file |
| `Ctrl+W` | Close file |
| `Ctrl+Shift+O` | Toggle OCR mode |
| `Ctrl+D` | Dictionary lookup |
| `Ctrl+Shift+T` | Toggle thumbnail panel |
| `Ctrl+Shift+P` | Toggle OCR panel |
| `Ctrl+B` | Toggle bookmark on current page |
| `Ctrl+Shift+B` | Show bookmarks list |
| `Ctrl+,` | Preferences |
| `Ctrl+Q` | Quit |

> On macOS, `Ctrl` is replaced by `Cmd`.

---

## Setting Up Anki Integration

1. Install the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on in Anki
2. Open Anki (it must be running for the integration to work)
3. In Tako Reader: **Tako Reader → Preferences → Anki**
4. Click **Test Connection** — decks and note types will populate automatically
5. Select your target **Deck** and **Note Type**
6. Map each Anki field to the appropriate Tako Reader data source (Word, Reading, Furigana, Definition, Sentence, Image)
7. Click **Save**

To add a card: look up a word via the dictionary popup, then click **+ Anki** next to the definition. `Ctrl+click` to review and edit the card fields before adding.

---

## Project Structure

```
tako-reader/
├── Install Tako Reader.bat        # Windows installer (double-click)
├── Install Tako Reader.command    # macOS installer (double-click)
├── Tako Reader.bat                # Windows launcher
├── Tako Reader.command            # macOS launcher
├── README.md
├── requirements.txt
│
├── src/                           # Application code
│   ├── tako_reader.py             # Main window and entry point
│   ├── utils.py                   # Shared helpers (icons, debug, paths)
│   ├── theme.py                   # Theme engine (4 themes + accent colours)
│   ├── widgets.py                 # UI widgets (page view, OCR panel, etc.)
│   ├── settings.py                # Settings dialog
│   ├── ocr.py                     # OCR subprocess/in-process manager
│   ├── dictionary.py              # Dictionary lookup and popup
│   ├── anki.py                    # AnkiConnect integration
│   ├── loaders.py                 # File format loaders (CBZ, PDF, etc.)
│   └── icons/
│       ├── dark/                  # Light-coloured icons (for dark themes)
│       └── light/                 # Dark-coloured icons (for light themes)
│
├── build_mac.sh                   # PyInstaller build (macOS)
├── build_windows.bat              # PyInstaller build (Windows)
└── .venv/                         # Created by installer
```

---

## Troubleshooting

### Dictionary popup shows no results
The jamdict database is not installed or not found.

```bash
# Check status
python -m jamdict info

# Install (macOS/Linux)
pip install jamdict jamdict-data

# Install (Windows)
pip install jamdict jamdict-data-fix
```

If `jamdict info` shows `[NG]` for the database path, set the `JAMDICT_HOME` environment variable to the directory where the data was installed.

### OCR panel indicator stays grey / first OCR is very slow
The OCR model (~400 MB) downloads on first use and loads into memory. This is normal — subsequent calls in the same session are fast. Enable **Settings → OCR → Load at Startup** to pre-load the model when the app opens.

### Windows: OCR / PyTorch DLL errors

If OCR fails with a `DLL initialization routine failed` error related to `c10.dll`, reinstall PyTorch as CPU-only:

```bash
pip uninstall torch torchvision torchaudio -y
pip cache purge
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Verify:
```bash
python -c "import torch; print(torch.__version__)"
# Expected: 2.x.x+cpu
```

> **Tip:** The install scripts (`Install Tako Reader.bat` / `.command`) already install CPU-only PyTorch by default, which avoids this issue entirely.

### Windows: Enabling CUDA

For GPU-accelerated OCR with NVIDIA GPUs:

```bash
pip uninstall torch -y
pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128
```

If CUDA initialises correctly, the GPU will appear in **Settings → OCR → Device**.

### Diagnosing OCR issues

Use **OCR → Check OCR Installation** in the menu bar. It reports manga-ocr status, PyTorch version, and any detected CUDA devices.

```bash
python -c "import torch; print(torch.__version__, '| CUDA:', torch.cuda.is_available())"
```

### macOS: "App can't be opened" / Gatekeeper blocks .command files

Right-click the `.command` file → **Open**, or run from Terminal:
```bash
chmod +x *.command
./Tako\ Reader.command
```

---

## Dependencies

| Package | Purpose |
|---|---|
| [PyQt6](https://pypi.org/project/PyQt6/) | GUI framework |
| [PyMuPDF](https://pymupdf.readthedocs.io/) | PDF rendering |
| [manga-ocr](https://github.com/kha-white/manga-ocr) | Japanese OCR (by Maciej Budyś) |
| [jamdict](https://github.com/neocl/jamdict) | Offline JMdict / KANJIDIC2 dictionary |
| [jamdict-data / jamdict-data-fix](https://pypi.org/project/jamdict-data-fix/) | Dictionary database |
| [Pillow](https://pillow.readthedocs.io/) | Image processing |
| [numpy](https://numpy.org/) | Array operations for OCR |
| [fugashi](https://github.com/polm/fugashi) | Japanese tokenisation |
| [pykakasi](https://github.com/miurahr/pykakasi) | Furigana generation |

Dictionary data licensed under [CC BY-SA 3.0](https://www.edrdg.org/edrdg/licence.html) by the Electronic Dictionary Research and Development Group.
