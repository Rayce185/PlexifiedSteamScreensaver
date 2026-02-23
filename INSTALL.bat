@echo off
setlocal EnableDelayedExpansion
title PSS Installer
set "LOGFILE=%~dp0install.log"

echo PSS INSTALL LOG > "!LOGFILE!"
echo Started: %date% %time% >> "!LOGFILE!"
echo Machine: %COMPUTERNAME% User: %USERNAME% >> "!LOGFILE!"
echo WorkDir: %~dp0 >> "!LOGFILE!"
echo.>> "!LOGFILE!"

echo.
echo  ====================================
echo  ====================================>> "!LOGFILE!"
echo   PSS - Plexified Steam Screensaver
echo   PSS - Plexified Steam Screensaver>> "!LOGFILE!"
echo   First-Time Setup
echo   First-Time Setup>> "!LOGFILE!"
echo  ====================================
echo  ====================================>> "!LOGFILE!"
echo.
echo.>> "!LOGFILE!"

:: --- Python ---
python --version > "%TEMP%\pss_pyver.txt" 2>&1
set PYERR=!errorlevel!
set /p PYVER=< "%TEMP%\pss_pyver.txt"
del "%TEMP%\pss_pyver.txt" 2>nul
if !PYERR! neq 0 (
    echo  [ERROR] Python not found. Install from https://www.python.org/downloads/
    echo  [ERROR] Python not found >> "!LOGFILE!"
    pause
    exit /b 1
)
echo  [OK] !PYVER!
echo  [OK] !PYVER! >> "!LOGFILE!"

:: --- pip ---
pip --version > "%TEMP%\pss_pipver.txt" 2>&1
set PIPERR=!errorlevel!
set /p PIPVER=< "%TEMP%\pss_pipver.txt"
del "%TEMP%\pss_pipver.txt" 2>nul
if !PIPERR! neq 0 (
    echo  [ERROR] pip not found.
    echo  [ERROR] pip not found >> "!LOGFILE!"
    pause
    exit /b 1
)
echo  [OK] !PIPVER!
echo  [OK] !PIPVER! >> "!LOGFILE!"

:: --- Dependencies ---
echo.
echo  Installing dependencies...
echo  Installing dependencies... >> "!LOGFILE!"
pip install -r "%~dp0requirements.txt" >> "!LOGFILE!" 2>&1
if errorlevel 1 (
    echo  [ERROR] pip install failed. See install.log
    pause
    exit /b 1
)
echo  [OK] Dependencies installed.
echo  [OK] Dependencies installed. >> "!LOGFILE!"

:: --- .env check ---
if exist "%~dp0.env" (
    echo.
    echo  [OK] .env already exists - skipping setup.
    echo  [OK] .env already exists >> "!LOGFILE!"
    goto :done
)

:: --- API Key ---
echo.
echo  -----------------------------------------------
echo  You need a Steam Web API Key to use PSS.
echo  Get one here: https://steamcommunity.com/dev/apikey
echo  -----------------------------------------------
echo.
set /p "STEAM_KEY=  Enter your Steam API Key: "
echo  API Key entered: [!STEAM_KEY:~0,4!...redacted] >> "!LOGFILE!"
if "!STEAM_KEY!"=="" (
    echo  [ERROR] No key entered.
    pause
    exit /b 1
)

:: --- Steam path ---
set "STEAM_DIR=C:\Program Files (x86)\Steam"
if exist "C:\Program Files (x86)\Steam\steam.exe" (
    echo  [OK] Steam found at default location.
    echo  [OK] Steam found at default location. >> "!LOGFILE!"
) else (
    echo  [WARN] Steam not at default path.
    set /p "STEAM_DIR=  Enter Steam install path: "
)
echo  Steam path: !STEAM_DIR! >> "!LOGFILE!"

:: --- Write .env ---
> "%~dp0.env" echo STEAM_API_KEY=!STEAM_KEY!
>> "%~dp0.env" echo STEAM_PATH=!STEAM_DIR!
if not exist "%~dp0.env" (
    echo  [ERROR] Failed to write .env
    pause
    exit /b 1
)
echo  [OK] Configuration saved to .env
echo  [OK] Configuration saved to .env >> "!LOGFILE!"

:: --- Create dirs ---
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
echo  Setup complete >> "!LOGFILE!"
echo  Finished: %date% %time% >> "!LOGFILE!"
pause
endlocal
exit /b 0
