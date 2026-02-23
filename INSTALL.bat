@echo off
setlocal EnableDelayedExpansion
title PSS Installer
echo.
echo  ====================================
echo   PSS - Plexified Steam Screensaver
echo   First-Time Setup
echo  ====================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo  Download it from https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
echo  [OK] Python found.

:: Install dependencies
echo.
echo  Installing dependencies...
pip install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo  [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo  [OK] Dependencies installed.

:: Check for existing .env
if exist "%~dp0.env" (
    echo.
    echo  [OK] .env already exists - skipping setup.
    goto :done
)

:: Prompt for Steam API key
echo.
echo  -----------------------------------------------
echo   You need a Steam Web API Key to use PSS.
echo   Get one here: https://steamcommunity.com/dev/apikey
echo  -----------------------------------------------
echo.
set /p "STEAM_KEY=  Enter your Steam API Key: "
if "!STEAM_KEY!"=="" (
    echo  [ERROR] No key entered. Create .env manually later.
    echo  See .env.example for the format.
    pause
    exit /b 1
)

:: Detect Steam path
set "STEAM_DIR=C:\Program Files ^(x86^)\Steam"
set "STEAM_CHECK=C:\Program Files (x86)\Steam\steam.exe"
if exist "!STEAM_CHECK!" (
    echo  [OK] Steam found at default location.
) else (
    echo.
    set /p "STEAM_DIR=  Steam install path: "
)

:: Write .env
echo STEAM_API_KEY=!STEAM_KEY!> "%~dp0.env"
echo STEAM_PATH=!STEAM_DIR!>> "%~dp0.env"
echo  [OK] Configuration saved to .env

:: Create dirs
if not exist "%~dp0data" mkdir "%~dp0data"
if not exist "%~dp0logs" mkdir "%~dp0logs"

:done
echo.
echo  ====================================
echo   Setup complete!
echo.
echo   To start PSS, double-click START.bat
echo  ====================================
echo.
pause
endlocal
