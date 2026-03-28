@echo off
REM ─── Tako Reader — Windows Installer ───────────────────────────────────────
REM One-time setup: creates a local Python environment with all dependencies.
REM Run this once, then use "Tako Reader.bat" to launch the app.
REM ────────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
title Tako Reader — Installing...

REM cd to the directory where this script lives
cd /d "%~dp0"

set VENV_DIR=.venv
set PYTHON_DIR=.python
set PYTHON_VERSION=3.11.9
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip
set PIP_URL=https://bootstrap.pypa.io/get-pip.py

echo.
echo   ====================================
echo     Tako Reader  —  Installer
echo   ====================================
echo.

REM ── Step 0: Check if already installed ─────────────────────────────────────
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo   [i] Existing installation found.
    echo.
    set /p REINSTALL="   Reinstall? This will update all packages. (y/N): "
    if /i not "!REINSTALL!"=="y" (
        echo   Skipping. Use "Tako Reader.bat" to launch.
        echo.
        pause
        exit /b 0
    )
    echo.
    echo   Removing old environment...
    rmdir /s /q "%VENV_DIR%" 2>nul
)

REM ── Step 1: Find or install Python ─────────────────────────────────────────
echo   [1/5] Checking for Python...

REM Try system Python first
where python >nul 2>&1
if %ERRORLEVEL%==0 (
    for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
    echo          Found: !PYVER!
    set PYTHON_CMD=python
    goto :has_python
)

REM Try python3
where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
    for /f "tokens=*" %%i in ('python3 --version 2^>^&1') do set PYVER=%%i
    echo          Found: !PYVER!
    set PYTHON_CMD=python3
    goto :has_python
)

REM No Python found — download embeddable
echo          Python not found. Downloading portable Python %PYTHON_VERSION%...
echo.

if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

REM Download using PowerShell (available on all modern Windows)
powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_DIR%\python.zip' }"
if %ERRORLEVEL% neq 0 (
    echo   [ERROR] Failed to download Python.
    echo   Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo          Extracting...
powershell -Command "Expand-Archive -Path '%PYTHON_DIR%\python.zip' -DestinationPath '%PYTHON_DIR%' -Force"
del "%PYTHON_DIR%\python.zip"

REM Enable pip in embeddable Python (uncomment "import site" in ._pth file)
for %%f in (%PYTHON_DIR%\python*._pth) do (
    powershell -Command "(Get-Content '%%f') -replace '#import site','import site' | Set-Content '%%f'"
)

REM Install pip
echo          Installing pip...
powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PIP_URL%' -OutFile '%PYTHON_DIR%\get-pip.py' }"
"%PYTHON_DIR%\python.exe" "%PYTHON_DIR%\get-pip.py" --no-warn-script-location >nul 2>&1
del "%PYTHON_DIR%\get-pip.py"

set PYTHON_CMD=%PYTHON_DIR%\python.exe
echo          Portable Python %PYTHON_VERSION% ready.
echo.

:has_python

REM ── Step 2: Create virtual environment ─────────────────────────────────────
echo   [2/5] Creating virtual environment...
%PYTHON_CMD% -m venv "%VENV_DIR%"
if %ERRORLEVEL% neq 0 (
    echo   [ERROR] Failed to create virtual environment.
    echo   Make sure Python 3.11+ is installed correctly.
    pause
    exit /b 1
)

REM Use the venv Python from now on
set PIP="%VENV_DIR%\Scripts\pip.exe"
set PYTHON="%VENV_DIR%\Scripts\python.exe"

REM Upgrade pip
%PYTHON% -m pip install --upgrade pip --quiet >nul 2>&1

REM ── Step 3: Install core packages ──────────────────────────────────────────
echo   [3/5] Installing core packages (PyQt6, PDF support, dictionary)...
echo          This may take a minute...
%PIP% install --quiet PyQt6 PyMuPDF Pillow numpy pykakasi
if %ERRORLEVEL% neq 0 (
    echo   [ERROR] Failed to install core packages.
    pause
    exit /b 1
)

REM ── Step 4: Install OCR engine ─────────────────────────────────────────────
echo   [4/5] Installing OCR engine (PyTorch + manga-ocr)...
echo          This downloads ~400 MB and may take several minutes...

REM Install CPU-only PyTorch (much smaller, avoids DLL hell)
%PIP% install --quiet torch --index-url https://download.pytorch.org/whl/cpu
if %ERRORLEVEL% neq 0 (
    echo   [WARNING] PyTorch installation failed. OCR may not work.
    echo            Continuing with remaining packages...
)

%PIP% install --quiet manga-ocr fugashi unidic-lite
if %ERRORLEVEL% neq 0 (
    echo   [WARNING] manga-ocr installation failed. OCR may not work.
)

REM ── Step 5: Install dictionary ─────────────────────────────────────────────
echo   [5/5] Installing dictionary database...
%PIP% install --quiet jamdict jamdict-data-fix
if %ERRORLEVEL% neq 0 (
    echo   [WARNING] Dictionary installation failed.
    echo            Lookup features may not work.
)

REM ── Done ───────────────────────────────────────────────────────────────────
echo.
echo   ====================================
echo     Installation complete!
echo   ====================================
echo.
echo   Use "Tako Reader.bat" to launch the app.
echo.
echo   The OCR model (~400 MB) will download
echo   automatically on first use.
echo.
pause
