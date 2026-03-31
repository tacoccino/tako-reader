#!/usr/bin/env bash
# ─── Tako Reader — Launcher ──────────────────────────────────────────────────
# Activates the local Python environment and runs the app.
# Run "install_mac.sh" first if this is your first time.
# ─────────────────────────────────────────────────────────────────────────────

# cd to the directory where this script lives (handles double-click from Finder)
cd "$(dirname "$0")"

VENV_DIR=".venv"

if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo ""
    echo "  Tako Reader is not installed yet."
    echo "  Please run 'Install Tako Reader.command' first."
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

# Launch the app in background and exit so Terminal can close
nohup "$VENV_DIR/bin/python" src/tako_reader.py "$@" >/dev/null 2>&1 &
exit 0
