@echo off
setlocal enabledelayedexpansion
title PSS Updater

echo ============================================
echo   PlexifiedSteamScreensaver - Updater
echo ============================================
echo.

set "PSS_DIR=%~dp0"
set "DATA_DIR=%PSS_DIR%data"
set "BACKUP_DIR=%PSS_DIR%_update_backup"
set "ZIP_URL=https://github.com/Rayce185/PlexifiedSteamScreensaver/archive/refs/heads/main.zip"
set "ZIP_FILE=%TEMP%\pss_update.zip"
set "EXTRACT_DIR=%TEMP%\pss_extract"

:: Check if server is running
tasklist /FI "WINDOWTITLE eq PSS*" 2>NUL | find /I "python" >NUL
if not errorlevel 1 (
    echo [!] PSS server appears to be running. Please stop it first.
    echo     Close the START.bat window, then run this again.
    pause
    exit /b 1
)

:: Step 1: Backup data folder
if exist "%DATA_DIR%" (
    echo [1/6] Backing up data folder...
    if exist "%BACKUP_DIR%" rmdir /S /Q "%BACKUP_DIR%"
    mkdir "%BACKUP_DIR%"
    xcopy "%DATA_DIR%\*" "%BACKUP_DIR%\" /E /I /Q >NUL
    echo       Backed up to _update_backup\
) else (
    echo [1/6] No data folder found - fresh install
)

:: Step 2: Download latest
echo [2/6] Downloading latest from GitHub...
powershell -Command "Invoke-WebRequest -Uri '%ZIP_URL%' -OutFile '%ZIP_FILE%'" 2>NUL
if not exist "%ZIP_FILE%" (
    echo [!] Download failed. Check your internet connection.
    echo     Your data is safe in _update_backup\
    pause
    exit /b 1
)

:: Step 3: Extract
echo [3/6] Extracting...
if exist "%EXTRACT_DIR%" rmdir /S /Q "%EXTRACT_DIR%"
powershell -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%EXTRACT_DIR%' -Force" 2>NUL

:: Step 4: Copy new files over (skip data folder)
echo [4/6] Updating files...
set "SRC=%EXTRACT_DIR%\PlexifiedSteamScreensaver-main"
if not exist "%SRC%" (
    echo [!] Extraction failed - unexpected folder structure.
    echo     Your data is safe in _update_backup\
    pause
    exit /b 1
)
:: Copy everything except data/
for %%F in ("%SRC%\*") do (
    copy /Y "%%F" "%PSS_DIR%" >NUL 2>NUL
)
:: Copy subdirectories except data
for /D %%D in ("%SRC%\*") do (
    set "DIRNAME=%%~nxD"
    if /I not "!DIRNAME!"=="data" (
        xcopy "%%D\*" "%PSS_DIR%!DIRNAME!\*" /E /I /Q /Y >NUL
    )
)

:: Step 5: Restore data folder
if exist "%BACKUP_DIR%" (
    echo [5/6] Restoring data...
    if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
    xcopy "%BACKUP_DIR%\*" "%DATA_DIR%\" /E /I /Q /Y >NUL
    rmdir /S /Q "%BACKUP_DIR%"
    echo       Data restored successfully.
) else (
    echo [5/6] No data to restore - will be created on first run.
)


:: Step 6: Install/update dependencies
echo [6/6] Installing dependencies...
pip install -r "%PSS_DIR%requirements.txt" >NUL 2>NUL
if errorlevel 1 (
    echo [!] pip install failed. Try running: pip install -r requirements.txt
) else (
    echo       Dependencies up to date.
)

:: Cleanup
del "%ZIP_FILE%" 2>NUL
rmdir /S /Q "%EXTRACT_DIR%" 2>NUL

echo.
echo ============================================
echo   Update complete!
echo   Run START.bat to launch the server.
echo ============================================
echo.
pause
