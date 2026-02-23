@echo off
setlocal EnableDelayedExpansion
title PSS Installer

:: Setup logging
set "LOGFILE=%~dp0install.log"
echo PSS INSTALL LOG > "!LOGFILE!"
echo Started: %date% %time% >> "!LOGFILE!"
echo Machine: %COMPUTERNAME% >> "!LOGFILE!"
echo User: %USERNAME% >> "!LOGFILE!"
echo WorkDir: %~dp0 >> "!LOGFILE!"
echo.>> "!LOGFILE!"

call :log "===================================="
call :log " PSS - Plexified Steam Screensaver"
call :log " First-Time Setup"
call :log "===================================="
call :logblank

:: Check Python
python --version > "%TEMP%\pss_pyver.txt" 2>&1
set PYERR=!errorlevel!
set /p PYVER=< "%TEMP%\pss_pyver.txt"
del "%TEMP%\pss_pyver.txt" 2>nul
if !PYERR! neq 0 (
    call :log "[ERROR] Python is not installed or not in PATH."
    call :log "Download it from https://www.python.org/downloads/"
    call :log "Make sure to check 'Add Python to PATH' during install."
    pause
    exit /b 1
)
call :log "[OK] %PYVER%"

:: Check pip
pip --version > "%TEMP%\pss_pipver.txt" 2>&1
set PIPERR=!errorlevel!
set /p PIPVER=< "%TEMP%\pss_pipver.txt"
del "%TEMP%\pss_pipver.txt" 2>nul
if !PIPERR! neq 0 (
    call :log "[ERROR] pip not found."
    pause
    exit /b 1
)
call :log "[OK] %PIPVER%"

:: Install dependencies
call :logblank
call :log "Installing dependencies..."
pip install -r "%~dp0requirements.txt" >> "!LOGFILE!" 2>&1
if errorlevel 1 (
    call :log "[ERROR] Failed to install dependencies. Check install.log"
    pause
    exit /b 1
)
call :log "[OK] Dependencies installed."

:: Check for existing .env — skip to done
if exist "%~dp0.env" (
    call :logblank
    call :log "[OK] .env already exists - skipping setup."
    goto :done
)

:: Prompt for Steam API key
call :logblank
call :log "-----------------------------------------------"
call :log " You need a Steam Web API Key to use PSS."
call :log " Get one here: https://steamcommunity.com/dev/apikey"
call :log "-----------------------------------------------"
call :logblank
set /p "STEAM_KEY=  Enter your Steam API Key: "
echo   API Key entered: [%STEAM_KEY:~0,4%...redacted] >> "!LOGFILE!"
if "!STEAM_KEY!"=="" (
    call :log "[ERROR] No key entered. Create .env manually later."
    pause
    exit /b 1
)

:: Detect Steam path — avoid parentheses in if blocks entirely
call :detect_steam
echo   Steam path: !STEAM_DIR! >> "!LOGFILE!"

:: Write .env
> "%~dp0.env" echo STEAM_API_KEY=!STEAM_KEY!
>> "%~dp0.env" echo STEAM_PATH=!STEAM_DIR!

if not exist "%~dp0.env" (
    call :log "[ERROR] Failed to write .env file!"
    pause
    exit /b 1
)
call :log "[OK] Configuration saved to .env"

:: Create dirs
if not exist "%~dp0data" mkdir "%~dp0data"
if not exist "%~dp0logs" mkdir "%~dp0logs"

:done
call :logblank
call :log "===================================="
call :log " Setup complete!"
call :logblank
call :log " To start PSS, double-click START.bat"
call :log "===================================="
echo.>> "!LOGFILE!"
echo Finished: %date% %time% >> "!LOGFILE!"
call :logblank
pause
endlocal
exit /b 0

:: --- Subroutines ---

:detect_steam
set "STEAM_DIR=C:\Program Files (x86)\Steam"
where steam.exe >nul 2>&1 && (
    call :log "[OK] Steam found."
    goto :eof
)
if exist "C:\Program Files (x86)\Steam\steam.exe" (
    call :log "[OK] Steam found at default location."
    goto :eof
)
call :log "[WARN] Steam not found at default location."
set /p "STEAM_DIR=  Enter Steam install path: "
goto :eof

:log
echo  %~1
echo  %~1 >> "!LOGFILE!"
goto :eof

:logblank
echo.
echo.>> "!LOGFILE!"
goto :eof
