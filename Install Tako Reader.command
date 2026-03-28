#!/usr/bin/env bash
# ─── Tako Reader — macOS / Linux Installer ───────────────────────────────────
# One-time setup: creates a local Python environment with all dependencies.
# Run this once, then use "Tako Reader.command" to launch the app.
# ─────────────────────────────────────────────────────────────────────────────

set -e

# cd to the directory where this script lives (handles double-click from Finder)
cd "$(dirname "$0")"

VENV_DIR=".venv"

echo ""
echo "  ===================================="
echo "    Tako Reader  —  Installer"
echo "  ===================================="
echo ""

# ── Step 0: Check if already installed ───────────────────────────────────────
if [ -f "$VENV_DIR/bin/python" ]; then
    echo "  [i] Existing installation found."
    echo ""
    read -p "   Reinstall? This will update all packages. (y/N): " REINSTALL
    if [[ ! "$REINSTALL" =~ ^[Yy]$ ]]; then
        echo "  Skipping. Use 'Tako Reader.command' to launch."
        echo ""
        exit 0
    fi
    echo ""
    echo "  Removing old environment..."
    rm -rf "$VENV_DIR"
fi

# ── Step 1: Find Python ──────────────────────────────────────────────────────
echo "  [1/5] Checking for Python 3..."

PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            echo "         Found: $($cmd --version)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo ""
    echo "  [ERROR] Python 3.11+ is required but not found."
    echo ""
    if [ "$(uname)" = "Darwin" ]; then
        echo "  Install via Homebrew:"
        echo "    brew install python@3.11"
        echo ""
        echo "  Or download from:"
        echo "    https://www.python.org/downloads/"
    else
        echo "  Install via your package manager, e.g.:"
        echo "    sudo apt install python3.11 python3.11-venv"
    fi
    echo ""
    exit 1
fi

# ── Step 2: Create virtual environment ───────────────────────────────────────
echo "  [2/5] Creating virtual environment..."
"$PYTHON_CMD" -m venv "$VENV_DIR"

# Use the venv Python/pip from now on
PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

# Upgrade pip quietly
"$PYTHON" -m pip install --upgrade pip --quiet >/dev/null 2>&1

# ── Step 3: Install core packages ────────────────────────────────────────────
echo "  [3/5] Installing core packages (PyQt6, PDF support, dictionary)..."
echo "         This may take a minute..."
"$PIP" install --quiet PyQt6 PyMuPDF Pillow numpy pykakasi
echo "         Done."

# ── Step 4: Install OCR engine ───────────────────────────────────────────────
echo "  [4/5] Installing OCR engine (PyTorch + manga-ocr)..."
echo "         This downloads ~400 MB and may take several minutes..."

"$PIP" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
if [ $? -ne 0 ]; then
    echo "  [WARNING] PyTorch installation failed. OCR may not work."
fi

"$PIP" install --quiet manga-ocr fugashi unidic-lite
if [ $? -ne 0 ]; then
    echo "  [WARNING] manga-ocr installation failed. OCR may not work."
fi

echo "         Done."

# ── Step 5: Install dictionary ───────────────────────────────────────────────
echo "  [5/5] Installing dictionary database..."

if [ "$(uname)" = "Darwin" ]; then
    "$PIP" install --quiet jamdict jamdict-data
else
    "$PIP" install --quiet jamdict jamdict-data
fi

if [ $? -ne 0 ]; then
    echo "  [WARNING] Dictionary installation failed."
    echo "           Lookup features may not work."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "  ===================================="
echo "    Installation complete!"
echo "  ===================================="
echo ""
echo "  Use 'Tako Reader.command' to launch the app."
echo ""
echo "  The OCR model (~400 MB) will download"
echo "  automatically on first use."
echo ""
read -p "  Press Enter to close..."
