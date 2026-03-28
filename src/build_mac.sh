#!/usr/bin/env bash
# ─── Tako Reader — macOS build script ────────────────────────────────────────
# Prerequisites:
#   pip install pyinstaller
#
# Usage:
#   chmod +x build_mac.sh && ./build_mac.sh
#
# Output:
#   dist/Tako Reader.app
# ─────────────────────────────────────────────────────────────────────────────

set -e

APP_NAME="Tako Reader"
ENTRY="tako_reader.py"
ICON="icons/app-icon.icns"   # optional — skip if not present

echo "🐙 Building Tako Reader for macOS..."
echo ""

# Clean previous builds
rm -rf build dist *.spec

# Build the icon flag only if the file exists
ICON_FLAG=""
if [ -f "$ICON" ]; then
    ICON_FLAG="--icon=$ICON"
    echo "  App icon:  $ICON"
else
    echo "  App icon:  (none — using default)"
fi

# Detect hidden imports that PyInstaller misses
# fugashi + unidic_lite: needed by manga-ocr's tokeniser
# jamdict / jamdict_data: dictionary lookup
HIDDEN_IMPORTS=(
    --hidden-import=fugashi
    --hidden-import=unidic_lite
    --hidden-import=jamdict
    --hidden-import=jamdict_data
    --hidden-import=pykakasi
    --hidden-import=PIL
    --hidden-import=numpy
    --hidden-import=manga_ocr
)

# Check if torch is installed — only add if present
if python3 -c "import torch" 2>/dev/null; then
    HIDDEN_IMPORTS+=(--hidden-import=torch)
    echo "  PyTorch:   detected — will be bundled"
else
    echo "  PyTorch:   not found — OCR will not work in the bundle"
fi

echo "  Entry:     $ENTRY"
echo ""

pyinstaller \
    --name="$APP_NAME" \
    --windowed \
    $ICON_FLAG \
    --add-data="icons:icons" \
    "${HIDDEN_IMPORTS[@]}" \
    --collect-all=torch \
    --collect-data=unidic_lite \
    --collect-data=jamdict_data \
    --collect-data=pykakasi \
    --collect-data=transformers \
    --noconfirm \
    --clean \
    "$ENTRY"

echo ""
echo "✅ Build complete!"
echo "   Output: dist/$APP_NAME.app"
echo ""
echo "   To run:  open \"dist/$APP_NAME.app\""
echo ""
echo "   Note: The OCR model (~400 MB) is NOT bundled."
echo "   It downloads to the HuggingFace cache on first use."
