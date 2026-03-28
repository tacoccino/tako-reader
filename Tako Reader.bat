@echo off
REM ─── Tako Reader — Launcher ────────────────────────────────────────────────
REM Activates the local Python environment and runs the app.
REM Run "Install Tako Reader.bat" first if this is your first time.
REM ────────────────────────────────────────────────────────────────────────────

setlocal
title Tako Reader

REM cd to the directory where this script lives
cd /d "%~dp0"

set VENV_DIR=.venv

REM Check that the venv exists
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo   Tako Reader is not installed yet.
    echo   Please run "Install Tako Reader.bat" first.
    echo.
    pause
    exit /b 1
)

REM Launch the app (start /b so this console closes immediately)
start "" "%VENV_DIR%\Scripts\pythonw.exe" src\tako_reader.py %*
