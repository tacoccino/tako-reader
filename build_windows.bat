@echo off
REM ─── Tako Reader — Windows build script ────────────────────────────────────
REM Prerequisites:
REM   pip install pyinstaller
REM
REM Usage:
REM   build_windows.bat
REM
REM Output:
REM   dist\Tako Reader\Tako Reader.exe
REM ────────────────────────────────────────────────────────────────────────────

setlocal
set APP_NAME=Tako Reader
set ENTRY=tako_reader.py
set ICON=icons\app-icon.ico

echo.
echo  === Tako Reader — Windows Build ===
echo.

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "Tako Reader.spec" del "Tako Reader.spec"

REM Build the icon flag
set ICON_FLAG=
if exist "%ICON%" (
    set ICON_FLAG=--icon=%ICON%
    echo   App icon:  %ICON%
) else (
    echo   App icon:  (none — using default)
)

echo   Entry:     %ENTRY%
echo.

pyinstaller ^
    --name="%APP_NAME%" ^
    --windowed ^
    %ICON_FLAG% ^
    --add-data="icons;icons" ^
    --hidden-import=fugashi ^
    --hidden-import=unidic_lite ^
    --hidden-import=jamdict ^
    --hidden-import=jamdict_data ^
    --hidden-import=pykakasi ^
    --hidden-import=PIL ^
    --hidden-import=numpy ^
    --hidden-import=manga_ocr ^
    --hidden-import=transformers ^
    --collect-all=torch ^
    --collect-data=unidic_lite ^
    --collect-data=jamdict_data ^
    --collect-data=jamdict_data_fix ^
    --collect-data=pykakasi ^
    --collect-data=transformers ^
    --noconfirm ^
    --clean ^
    "%ENTRY%"

echo.
if %ERRORLEVEL% neq 0 (
    echo  BUILD FAILED
    pause
    exit /b 1
)

echo  Build complete!
echo.
echo   Output: dist\%APP_NAME%\%APP_NAME%.exe
echo.
echo   Note: The OCR model (~400 MB) is NOT bundled.
echo   It downloads to the HuggingFace cache on first use.
echo.
pause
