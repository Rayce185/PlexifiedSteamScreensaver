@echo off
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

:: Check for .env
if exist "%~dp0.env" (
    echo.
    echo  [OK] .env already exists - skipping API key setup.
    goto :check_steam
)

:: Prompt for Steam API key
echo.
echo  -----------------------------------------------
echo   You need a Steam Web API Key to use PSS.
echo   Get one here: https://steamcommunity.com/dev/apikey
echo  -----------------------------------------------
echo.
set /p STEAM_KEY="  Enter your Steam API Key: "
if "%STEAM_KEY%"=="" (
    echo  [ERROR] No key entered. You can create .env manually later.
    echo  See .env.example for the format.
    pause
    exit /b 1
)

:: Detect Steam path
:check_steam
set "STEAM_DEFAULT=C:\Program Files (x86)\Steam"
if exist "%STEAM_DEFAULT%\steam.exe" (
    set "STEAM_DIR=%STEAM_DEFAULT%"
    echo  [OK] Steam found at %STEAM_DEFAULT%
) else (
    echo.
    set /p STEAM_DIR="  Steam install path (or press Enter for default): "
    if "%STEAM_DIR%"=="" set "STEAM_DIR=%STEAM_DEFAULT%"
)

:: Write .env (only if it doesn't exist)
if not exist "%~dp0.env" (
    echo STEAM_API_KEY=%STEAM_KEY%> "%~dp0.env"
    echo STEAM_PATH=%STEAM_DIR%>> "%~dp0.env"
    echo  [OK] Configuration saved to .env
)

:: Create data/logs dirs
if not exist "%~dp0data" mkdir "%~dp0data"
if not exist "%~dp0logs" mkdir "%~dp0logs"

echo.
echo  ====================================
echo   Setup complete!
echo.
echo   To start PSS, double-click START.bat
echo   or run: python -m pss.server
echo  ====================================
echo.
pause
