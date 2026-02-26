@echo off
title PSS - Plexified Steam Screensaver
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: ============================================
::  PSS Universal Launcher
::  Double-click this file. That's it.
:: ============================================

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ============================================
    echo   Python is not installed.
    echo  ============================================
    echo.
    echo   PSS needs Python to run. Download it here:
    echo.
    echo   https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: Check "Add Python to PATH" during install!
    echo.
    echo   After installing Python, double-click PSS.bat again.
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

:: Check if dependencies are installed
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Installing dependencies...
    pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo  [ERROR] Dependency install failed.
        pause
        exit /b 1
    )
    echo  [OK] Dependencies installed.
)

:: Check .env exists
if not exist "%~dp0.env" (
    echo.
    echo  ============================================
    echo   First-Time Setup
    echo  ============================================
    echo.
    echo   You need a Steam Web API Key to use PSS.
    echo.
    echo   1. Open: https://steamcommunity.com/dev/apikey
    echo   2. Log in with your Steam account
    echo   3. Enter any domain name ^(e.g. "localhost"^)
    echo   4. Copy the key shown
    echo.
    start "" "https://steamcommunity.com/dev/apikey"
    echo.
    set /p "STEAM_KEY=  Paste your Steam API Key here: "
    if "!STEAM_KEY!"=="" (
        echo  [ERROR] No key entered.
        pause
        exit /b 1
    )
    echo STEAM_API_KEY=!STEAM_KEY!> "%~dp0.env"
    echo  [OK] API key saved.
    echo.
    if not exist "%~dp0data" mkdir "%~dp0data"
    if not exist "%~dp0logs" mkdir "%~dp0logs"
)

:: Check pystray (for tray mode)
python -c "import pystray" >nul 2>&1
if errorlevel 1 (
    echo  Installing tray dependencies...
    pip install pystray Pillow >nul 2>&1
)

:: Launch tray app
echo.
echo  Starting PSS...
echo  Look for the PSS icon in your system tray (bottom-right).
echo  You can close this window.
echo.
start "" pythonw "%~dp0pss_tray.pyw"
timeout /t 3 /nobreak >nul
exit /b 0
