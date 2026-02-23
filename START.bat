@echo off
setlocal EnableDelayedExpansion
title PSS - Plexified Steam Screensaver
cd /d "%~dp0"

:: Setup logging
set "LOGFILE=%~dp0start.log"
echo PSS START LOG > "!LOGFILE!"
echo Started: %date% %time% >> "!LOGFILE!"
echo Machine: %COMPUTERNAME% >> "!LOGFILE!"
echo WorkDir: %~dp0 >> "!LOGFILE!"
echo.>> "!LOGFILE!"

:: Check .env
if not exist ".env" (
    call :log "[ERROR] .env not found - run INSTALL.bat first!"
    pause
    exit /b 1
)
call :log "[OK] .env found"

:: Check Python
python --version > "%TEMP%\pss_pyver.txt" 2>&1
set PYERR=!errorlevel!
set /p PYVER=< "%TEMP%\pss_pyver.txt"
del "%TEMP%\pss_pyver.txt" 2>nul
if !PYERR! neq 0 (
    call :log "[ERROR] Python not found - run INSTALL.bat first!"
    pause
    exit /b 1
)
call :log "[OK] %PYVER%"

:: Check if fastapi is importable
python -c "import fastapi; print(f'fastapi {fastapi.__version__}')" > "%TEMP%\pss_fa.txt" 2>&1
set FAERR=!errorlevel!
set /p FAVER=< "%TEMP%\pss_fa.txt"
del "%TEMP%\pss_fa.txt" 2>nul
if !FAERR! neq 0 (
    call :log "[ERROR] FastAPI not installed - run INSTALL.bat first!"
    pause
    exit /b 1
)
call :log "[OK] %FAVER%"

:: Log .env contents (redacted)
call :logblank
call :log "Config:"
for /f "usebackq tokens=1,2 delims==" %%a in (".env") do (
    if /i "%%a"=="STEAM_API_KEY" (
        echo   %%a=****redacted**** >> "!LOGFILE!"
    ) else (
        echo   %%a=%%b >> "!LOGFILE!"
    )
)

:: Check database
if exist "data\pss.db" (
    call :log "[OK] Database exists"
) else (
    call :log "[INFO] No database yet - first run will fetch your Steam library"
)

:: Open browser after delay
call :logblank
call :log "Opening browser..."
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8787/customizer"

:: Launch server
call :logblank
call :log "===================================="
call :log " PSS is running!"
call :logblank
call :log " Customizer: http://localhost:8787/customizer"
call :log " Screensaver: http://localhost:8787/screensaver"
call :logblank
call :log " Keep this window open while using PSS."
call :log " Close this window or press Ctrl+C to stop."
call :log "===================================="
call :logblank

python -m pss.server

:: Server exited
echo.>> "!LOGFILE!"
echo Server exited: %date% %time% >> "!LOGFILE!"
echo Exit code: !errorlevel! >> "!LOGFILE!"
call :logblank
call :log "Server stopped."
pause
endlocal
exit /b 0

:log
echo  %~1
echo  %~1 >> "!LOGFILE!"
goto :eof

:logblank
echo.
echo.>> "!LOGFILE!"
goto :eof
