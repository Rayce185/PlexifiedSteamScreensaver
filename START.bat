@echo off
title PSS - Plexified Steam Screensaver
cd /d "%~dp0"

:: Quick sanity checks
if not exist ".env" (
    echo  .env not found - run INSTALL.bat first!
    pause
    exit /b 1
)
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python not found - run INSTALL.bat first!
    pause
    exit /b 1
)

:: Open browser after a short delay (in background)
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8787/customizer"

:: Launch server (stays in foreground so user can see logs + Ctrl+C to stop)
echo  Starting PSS server...
echo  Press Ctrl+C to stop.
echo.
python -m pss.server
pause
